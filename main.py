import os
import sys
import requests
import re
import json
import subprocess
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QFileDialog, QWidget, QProgressBar,
    QMessageBox, QListWidget, QCheckBox, QGroupBox, QGridLayout
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QDesktopServices

class ReleasesFetcher(QThread):
    """Thread for fetching available chunker releases from GitHub"""
    releases_fetched = pyqtSignal(list)
    error_occurred = pyqtSignal(str)
    
    def run(self):
        try:
            # GitHub API to get releases
            url = "https://api.github.com/repos/HiveGamesOSS/Chunker/releases"
            response = requests.get(url)
            if response.status_code != 200:
                self.error_occurred.emit(f"Failed to fetch releases: {response.status_code}")
                return
                
            releases = []
            for release in response.json():
                tag_name = release["tag_name"]
                # Find the chunker-cli jar asset
                for asset in release["assets"]:
                    if asset["name"].startswith("chunker-cli-") and asset["name"].endswith(".jar"):
                        releases.append({
                            "version": tag_name,
                            "jar_name": asset["name"],
                            "download_url": asset["browser_download_url"],
                            "release_date": datetime.strptime(release["published_at"], "%Y-%m-%dT%H:%M:%SZ")
                        })
                        break
                        
            # Sort by release date (newest first)
            releases.sort(key=lambda x: x["release_date"], reverse=True)
            self.releases_fetched.emit(releases)
        except Exception as e:
            self.error_occurred.emit(f"Error fetching releases: {str(e)}")

class DownloadThread(QThread):
    """Thread for downloading chunker-cli.jar"""
    progress_updated = pyqtSignal(int)
    download_complete = pyqtSignal(str)
    download_error = pyqtSignal(str)
    
    def __init__(self, url, save_path):
        super().__init__()
        self.url = url
        self.save_path = save_path
        
    def run(self):
        try:
            response = requests.get(self.url, stream=True)
            if response.status_code != 200:
                self.download_error.emit(f"Failed to download file: {response.status_code}")
                return
                
            total_size = int(response.headers.get('content-length', 0))
            block_size = 1024  # 1 Kibibyte
            downloaded = 0
            
            with open(self.save_path, 'wb') as file:
                for data in response.iter_content(block_size):
                    downloaded += len(data)
                    file.write(data)
                    if total_size:
                        percent = int(downloaded * 100 / total_size)
                        self.progress_updated.emit(percent)
                        
            self.download_complete.emit(self.save_path)
        except Exception as e:
            self.download_error.emit(f"Error downloading file: {str(e)}")
            if os.path.exists(self.save_path):
                os.remove(self.save_path)  # Clean up partially downloaded file

class ChunkerBatchConverter(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Chunker Batch Converter")
        self.setMinimumSize(600, 500)
        self.releases = []
        self.selected_version = None
        self.jar_path = None
        self.selected_input_dir = None
        self.selected_output_dir = None
        self.custom_java_path = None  # Store custom Java path
        self.formats = {
            "Java": [
                "JAVA_1_8_8",
                "JAVA_1_9", "JAVA_1_9_3",
                "JAVA_1_10", "JAVA_1_10_2",
                "JAVA_1_11", "JAVA_1_11_2", 
                "JAVA_1_12", "JAVA_1_12_2",
                "JAVA_1_13", "JAVA_1_13_2",
                "JAVA_1_14", "JAVA_1_14_4",
                "JAVA_1_15", "JAVA_1_15_2", 
                "JAVA_1_16", "JAVA_1_16_5",
                "JAVA_1_17", "JAVA_1_17_1",
                "JAVA_1_18", "JAVA_1_18_2",
                "JAVA_1_19", "JAVA_1_19_4",
                "JAVA_1_20", "JAVA_1_20_5", "JAVA_1_20_6",
                "JAVA_1_21", "JAVA_1_21_5"
            ],
            "Bedrock": [
                "BEDROCK_1_12",
                "BEDROCK_1_13", "BEDROCK_1_13_60",
                "BEDROCK_1_14", "BEDROCK_1_14_60",
                "BEDROCK_1_16", "BEDROCK_1_16_220",
                "BEDROCK_1_17", "BEDROCK_1_17_40",
                "BEDROCK_1_18", "BEDROCK_1_18_30",
                "BEDROCK_1_19", "BEDROCK_1_19_80",
                "BEDROCK_1_20", "BEDROCK_1_20_80",
                "BEDROCK_1_21", "BEDROCK_1_21_70"
            ]
        }
        
        self.init_ui()
        self.check_jar_and_fetch_releases()
    
    def init_ui(self):
        # Main layout
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        
        # Release selection area
        self.release_group = QGroupBox("Chunker-CLI Selection")
        release_layout = QVBoxLayout()
        
        release_info_layout = QHBoxLayout()
        self.release_label = QLabel("Available Versions:")
        self.release_combo = QComboBox()
        self.release_combo.currentIndexChanged.connect(self.on_version_selected)
        release_info_layout.addWidget(self.release_label)
        release_info_layout.addWidget(self.release_combo)
        
        self.jar_status_label = QLabel("Status: Checking for chunker-cli.jar...")
        
        release_button_layout = QHBoxLayout()
        self.download_button = QPushButton("Download Selected Version")
        self.download_button.clicked.connect(self.download_selected_version)
        self.download_button.setEnabled(False)
        
        self.browse_jar_button = QPushButton("Browse for JAR")
        self.browse_jar_button.clicked.connect(self.browse_for_jar)
        
        self.refresh_button = QPushButton("Refresh Releases")
        self.refresh_button.clicked.connect(self.check_jar_and_fetch_releases)
        
        release_button_layout.addWidget(self.download_button)
        release_button_layout.addWidget(self.browse_jar_button)
        release_button_layout.addWidget(self.refresh_button)
        
        self.download_progress = QProgressBar()
        self.download_progress.setVisible(False)
        
        release_layout.addLayout(release_info_layout)
        release_layout.addWidget(self.jar_status_label)
        release_layout.addLayout(release_button_layout)
        release_layout.addWidget(self.download_progress)
        self.release_group.setLayout(release_layout)
        
        # Batch conversion area
        conversion_group = QGroupBox("Batch Conversion")
        conversion_layout = QGridLayout()
        
        # Java executable path selection
        conversion_layout.addWidget(QLabel("Java Path:"), 0, 0)
        self.java_path_label = QLabel("System Default")
        conversion_layout.addWidget(self.java_path_label, 0, 1)
        self.browse_java_button = QPushButton("Select Java")
        self.browse_java_button.clicked.connect(self.browse_for_java)
        conversion_layout.addWidget(self.browse_java_button, 0, 2)
        
        # Input directory selection
        conversion_layout.addWidget(QLabel("Input Directory:"), 1, 0)
        self.input_dir_label = QLabel("Not selected")
        conversion_layout.addWidget(self.input_dir_label, 1, 1)
        self.browse_input_button = QPushButton("Browse")
        self.browse_input_button.clicked.connect(self.browse_input_dir)
        conversion_layout.addWidget(self.browse_input_button, 1, 2)
        
        # Output directory selection
        conversion_layout.addWidget(QLabel("Output Directory:"), 2, 0)
        self.output_dir_label = QLabel("Not selected")
        conversion_layout.addWidget(self.output_dir_label, 2, 1)
        self.browse_output_button = QPushButton("Browse")
        self.browse_output_button.clicked.connect(self.browse_output_dir)
        conversion_layout.addWidget(self.browse_output_button, 2, 2)
        
        # Format Selection
        conversion_layout.addWidget(QLabel("Target Format:"), 3, 0)
        
        # Format type selector (Java/Bedrock)
        self.format_type_combo = QComboBox()
        self.format_type_combo.addItems(["Java", "Bedrock"])
        self.format_type_combo.currentTextChanged.connect(self.update_format_versions)
        conversion_layout.addWidget(self.format_type_combo, 3, 1)
        
        # Format version selector
        self.format_version_combo = QComboBox()
        conversion_layout.addWidget(self.format_version_combo, 3, 2)
        
        # Initialize format versions
        self.update_format_versions("Java")
        
        # Convert button
        self.convert_button = QPushButton("Start Conversion")
        self.convert_button.clicked.connect(self.start_conversion)
        self.convert_button.setEnabled(False)
        conversion_layout.addWidget(self.convert_button, 4, 0, 1, 3)
        
        conversion_group.setLayout(conversion_layout)
        
        # Status area
        status_group = QGroupBox("Status")
        status_layout = QVBoxLayout()
        
        self.status_label = QLabel("Ready")
        self.status_list = QListWidget()
        
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.status_list)
        
        status_group.setLayout(status_layout)
        
        # Add everything to main layout
        main_layout.addWidget(self.release_group)
        main_layout.addWidget(conversion_group)
        main_layout.addWidget(status_group)
        
        # Set main widget
        self.setCentralWidget(main_widget)
    
    def update_format_versions(self, format_type):
        """Update the format versions dropdown based on the selected format type"""
        self.format_version_combo.clear()
        if format_type in self.formats:
            for version in self.formats[format_type]:
                self.format_version_combo.addItem(version)
            # Select the latest version by default
            if self.format_version_combo.count() > 0:
                self.format_version_combo.setCurrentIndex(self.format_version_combo.count() - 1)
    
    def check_jar_and_fetch_releases(self):
        """Check if chunker-cli.jar exists and fetch available releases"""
        self.jar_status_label.setText("Status: Checking for chunker-cli.jar...")
        self.download_button.setEnabled(False)
        self.convert_button.setEnabled(False)
        
        # Check if jar exists in the application directory
        jar_files = [f for f in os.listdir('.') if f.startswith('chunker-cli-') and f.endswith('.jar')]
        
        if jar_files:
            # Use the first jar file found
            self.jar_path = os.path.abspath(jar_files[0])
            self.jar_status_label.setText(f"Status: Found {os.path.basename(self.jar_path)}")
            # Only enable convert button if both input and output dirs are selected
            if self.selected_input_dir and self.selected_output_dir:
                self.convert_button.setEnabled(True)
            self.update_status_list(f"Using {os.path.basename(self.jar_path)}")
        else:
            self.jar_status_label.setText("Status: No chunker-cli.jar found")
            self.update_status_list("No chunker-cli.jar found - please download or browse for one")
        
        # Fetch available releases
        self.fetch_releases()
    
    def fetch_releases(self):
        """Fetch available releases from GitHub"""
        self.release_label.setText("Available Versions: Loading...")
        self.release_combo.clear()
        
        self.fetcher = ReleasesFetcher()
        self.fetcher.releases_fetched.connect(self.on_releases_fetched)
        self.fetcher.error_occurred.connect(self.on_fetch_error)
        self.fetcher.start()
    
    def on_releases_fetched(self, releases):
        """Handle fetched releases data"""
        self.releases = releases
        self.release_combo.clear()
        
        if not releases:
            self.release_label.setText("Available Versions: None found")
            return
            
        for release in releases:
            self.release_combo.addItem(f"{release['version']} - {release['jar_name']}", release)
        
        self.release_label.setText(f"Available Versions ({len(releases)} found):")
        self.download_button.setEnabled(True)
        
        # Auto-select latest version
        if releases:
            self.release_combo.setCurrentIndex(0)
            self.on_version_selected(0)
            self.update_status_list(f"Found {len(releases)} available versions")
    
    def on_fetch_error(self, error_msg):
        """Handle error in fetching releases"""
        self.release_label.setText("Available Versions: Error fetching")
        QMessageBox.critical(self, "Error", error_msg)
        self.update_status_list(f"Error: {error_msg}")
    
    def on_version_selected(self, index):
        """Handle version selection from dropdown"""
        if index >= 0 and index < len(self.releases):
            self.selected_version = self.releases[index]
    
    def download_selected_version(self):
        """Download the selected chunker-cli.jar version"""
        if not self.selected_version:
            QMessageBox.warning(self, "Warning", "No version selected")
            return
        
        # Ask for confirmation
        reply = QMessageBox.question(
            self, 
            'Confirm Download',
            f"Download {self.selected_version['jar_name']}?\nSize: ~30MB",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
            QMessageBox.StandardButton.Yes
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            download_url = self.selected_version['download_url']
            save_path = os.path.abspath(self.selected_version['jar_name'])
            
            # Start download
            self.download_progress.setVisible(True)
            self.download_progress.setValue(0)
            self.jar_status_label.setText(f"Status: Downloading {self.selected_version['jar_name']}...")
            self.download_button.setEnabled(False)
            
            self.download_thread = DownloadThread(download_url, save_path)
            self.download_thread.progress_updated.connect(self.update_download_progress)
            self.download_thread.download_complete.connect(self.download_finished)
            self.download_thread.download_error.connect(self.download_error)
            self.download_thread.start()
            
            self.update_status_list(f"Downloading {self.selected_version['jar_name']}...")
    
    def update_download_progress(self, percent):
        """Update download progress bar"""
        self.download_progress.setValue(percent)
    
    def download_finished(self, jar_path):
        """Handle download completion"""
        self.jar_path = jar_path
        self.download_progress.setVisible(False)
        self.jar_status_label.setText(f"Status: Downloaded {os.path.basename(jar_path)}")
        self.download_button.setEnabled(True)
        self.convert_button.setEnabled(self.selected_input_dir and self.selected_output_dir)
        self.update_status_list(f"Successfully downloaded {os.path.basename(jar_path)}")
    
    def download_error(self, error_msg):
        """Handle download error"""
        self.download_progress.setVisible(False)
        self.jar_status_label.setText("Status: Download failed")
        self.download_button.setEnabled(True)
        QMessageBox.critical(self, "Download Error", error_msg)
        self.update_status_list(f"Error: {error_msg}")
    
    def browse_for_jar(self):
        """Browse for an existing chunker-cli.jar file"""
        jar_file, _ = QFileDialog.getOpenFileName(
            self,
            "Select chunker-cli JAR File",
            "",
            "JAR Files (*.jar)"
        )
        
        if jar_file:
            self.jar_path = jar_file
            self.jar_status_label.setText(f"Status: Selected {os.path.basename(jar_file)}")
            self.convert_button.setEnabled(self.selected_input_dir and self.selected_output_dir)
            self.update_status_list(f"Using {os.path.basename(jar_file)}")
    
    def browse_for_java(self):
        """Browse for a Java executable file to use with Chunker"""
        java_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Java Executable",
            "",
            "Executable Files (*.exe);;All Files (*)" if os.name == 'nt' else "All Files (*)"
        )
        
        if java_path:
            self.custom_java_path = java_path
            self.java_path_label.setText(os.path.basename(java_path))
            self.update_status_list(f"Using custom Java: {java_path}")
            
            # Test the Java version
            self.check_specific_java_version(java_path)
    
    def check_specific_java_version(self, java_path):
        """Check if the specific Java path is valid and compatible"""
        try:
            process = subprocess.Popen(
                [java_path, "-version"], 
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            stdout, stderr = process.communicate()
            
            # Java version info is typically in stderr
            output = stderr if stderr else stdout
            
            # Extract version number
            version_match = re.search(r'version "([^"]+)"', output)
            if not version_match:
                QMessageBox.warning(self, "Java Version Unknown", 
                                   f"Could not detect version for selected Java executable.")
                return
                
            version_str = version_match.group(1)
            
            # Parse version - handle both legacy (1.8.0) and modern (17.0.2) formats
            if version_str.startswith("1."):
                major_version = int(version_str.split(".")[1])
            else:
                major_version = int(version_str.split(".")[0])
                
            if major_version < 17:
                QMessageBox.warning(self, "Java Version Warning", 
                                   f"Selected Java version ({version_str}) may be too old.\n"
                                   "Chunker requires Java 17 or newer.")
            else:
                QMessageBox.information(self, "Java Version Compatible", 
                                      f"Selected Java version ({version_str}) is compatible with Chunker.")
                
            self.update_status_list(f"Detected Java {version_str} at selected path")
            
        except Exception as e:
            QMessageBox.critical(self, "Java Check Failed", 
                              f"Error checking Java version: {str(e)}\n"
                              "Selected file may not be a valid Java executable.")
    
    def browse_input_dir(self):
        """Browse for input directory containing world files"""
        directory = QFileDialog.getExistingDirectory(self, "Select Input Directory")
        if directory:
            self.selected_input_dir = directory
            self.input_dir_label.setText(os.path.basename(directory) or directory)
            self.convert_button.setEnabled(bool(self.jar_path and self.selected_output_dir))
            self.update_status_list(f"Selected input directory: {directory}")
    
    def browse_output_dir(self):
        """Browse for output directory"""
        directory = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if directory:
            self.selected_output_dir = directory
            self.output_dir_label.setText(os.path.basename(directory) or directory)
            self.convert_button.setEnabled(bool(self.jar_path and self.selected_input_dir))
            self.update_status_list(f"Selected output directory: {directory}")
    
    def start_conversion(self):
        """Start the batch conversion process"""
        if not (self.jar_path and self.selected_input_dir and self.selected_output_dir):
            QMessageBox.warning(self, "Warning", "Please select JAR file, input and output directories")
            return
        
        # Validate conversion options
        target_format = self.format_type_combo.currentText()
        target_version = self.format_version_combo.currentText()
        
        if not target_version:
            QMessageBox.warning(self, "Warning", "Please select a target format version")
            return
        
        # Check Java version compatibility
        if not self.check_java_version():
            return
        
        # Start conversion
        self.update_status_list("Starting conversion process...")
        self.status_label.setText("Status: Converting...")
        self.convert_button.setEnabled(False)
        
        # Process all worlds in input directory
        input_dirs = [d for d in os.listdir(self.selected_input_dir) 
                     if os.path.isdir(os.path.join(self.selected_input_dir, d))]
        
        # Check if we have any potential world directories
        if not input_dirs:
            QMessageBox.warning(self, "Warning", "No directories found in the input directory")
            self.convert_button.setEnabled(True)
            self.status_label.setText("Status: No worlds found")
            return
        
        # Process each world
        success_count = 0
        for world_dir in input_dirs:
            full_input_path = os.path.join(self.selected_input_dir, world_dir)
            self.update_status_list(f"Processing world: {world_dir}")
            
            # Check if it looks like a Minecraft world
            if not self.is_minecraft_world(full_input_path):
                self.update_status_list(f"Skipping {world_dir} - doesn't look like a Minecraft world")
                continue
                
            # Prepare and run conversion commands
            self.convert_world(full_input_path, target_format, target_version)
            success_count += 1
        
        # Update status
        self.convert_button.setEnabled(True)
        if success_count > 0:
            self.status_label.setText(f"Status: Conversion complete ({success_count} conversions)")
            self.update_status_list("Conversion process completed")
        else:
            self.status_label.setText("Status: No worlds converted")
            self.update_status_list("No worlds were converted")
    
    def is_minecraft_world(self, directory):
        """Check if the directory looks like a Minecraft world"""
        # Check for common files that might indicate a Minecraft world
        java_indicators = ['level.dat', 'session.lock']
        bedrock_indicators = ['level.dat', 'db']
        
        files = os.listdir(directory)
        
        # Check Java world indicators
        for indicator in java_indicators:
            if indicator in files:
                return True
                
        # Check Bedrock world indicators
        if 'db' in files and os.path.isdir(os.path.join(directory, 'db')):
            return True
            
        return False
    
    def convert_world(self, input_path, target_type, target_version):
        """Convert a world to the specified format using chunker-cli"""
        world_name = os.path.basename(input_path)
        output_dir_name = f"{world_name}_{target_version.lower()}"
        target_dir = os.path.join(self.selected_output_dir, output_dir_name)
        
        # Create target directory if it doesn't exist
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)
        
        # Build command according to the requirements:
        # java -jar chunker-cli-VERSION.jar -i "my_world" -f BEDROCK_1_20_80 -o output
        cmd = [
            self.custom_java_path if self.custom_java_path else "java", "-jar", self.jar_path,
            "-i", input_path,
            "-o", target_dir,
            "-f", target_version  # Format like JAVA_1_20_5 or BEDROCK_1_20_80
        ]
        
        self.update_status_list(f"Converting {world_name} to {target_version}...")
        
        try:
            # Run the command
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            stdout, stderr = process.communicate()
            
            if process.returncode == 0:
                self.update_status_list(f"Successfully converted {world_name} to {target_version}")
            else:
                self.update_status_list(f"Error converting {world_name}: {stderr}")
                # Show detailed error in a dialog
                QMessageBox.critical(self, "Conversion Error", 
                                    f"Error converting {world_name}:\n\n{stderr}")
        except Exception as e:
            error_msg = str(e)
            self.update_status_list(f"Exception during conversion: {error_msg}")
            QMessageBox.critical(self, "Conversion Exception", 
                                f"Exception during conversion of {world_name}:\n\n{error_msg}")
    
    def check_java_version(self):
        """Check if Java is installed and its version is compatible"""
        try:
            process = subprocess.Popen(
                [self.custom_java_path if self.custom_java_path else "java", "-version"], 
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            stdout, stderr = process.communicate()
            
            # Java version info is typically in stderr
            output = stderr if stderr else stdout
            
            if "not recognized" in output.lower() or "no java" in output.lower():
                QMessageBox.critical(self, "Java Not Found", 
                                   "Java is not installed or not in the system PATH.\n"
                                   "Please install Java 17 or newer to use Chunker.")
                return False
            
            # Extract version number - typical output contains: "java version "1.8.0_XXX"" or "openjdk version "17.0.2""
            version_match = re.search(r'version "([^"]+)"', output)
            if not version_match:
                QMessageBox.warning(self, "Java Version Unknown", 
                                   "Could not detect Java version.\n"
                                   "Chunker requires Java 17 or newer.")
                self.update_status_list("Warning: Could not detect Java version")
                return True  # Let the user try anyway
                
            version_str = version_match.group(1)
            
            # Parse version - handle both legacy (1.8.0) and modern (17.0.2) formats
            if version_str.startswith("1."):
                major_version = int(version_str.split(".")[1])
            else:
                major_version = int(version_str.split(".")[0])
                
            if major_version < 17:
                QMessageBox.critical(self, "Java Version Incompatible", 
                                   f"Detected Java version: {version_str}\n"
                                   "Chunker requires Java 17 or newer.\n\n"
                                   "The error you're seeing (UnsupportedClassVersionError) is because your "
                                   "Java version is too old to run this JAR file.")
                self.update_status_list(f"Error: Java {version_str} is too old, version 17+ required")
                return False
                
            self.update_status_list(f"Using Java version {version_str}")
            return True
            
        except Exception as e:
            QMessageBox.warning(self, "Java Check Failed", 
                             f"Could not check Java version: {str(e)}\n"
                             "Please ensure you have Java 17 or newer installed.")
            self.update_status_list(f"Warning: Java version check failed: {str(e)}")
            return True  # Let the user try anyway
    
    def update_status_list(self, message):
        """Add a message to the status list widget"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.status_list.addItem(f"[{timestamp}] {message}")
        self.status_list.scrollToBottom()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ChunkerBatchConverter()
    window.show()
    sys.exit(app.exec())