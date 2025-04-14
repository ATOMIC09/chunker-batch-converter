import os
import sys
import requests
import re
import json
import subprocess
import threading
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QFileDialog, QWidget, QProgressBar,
    QMessageBox, QListWidget, QCheckBox, QGroupBox, QGridLayout, QSizePolicy, QLineEdit
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

class ConversionThread(QThread):
    """Thread for running chunker conversions without freezing the GUI"""
    progress_updated = pyqtSignal(str, int)  # world_name, percentage
    world_completed = pyqtSignal(str, bool, str)  # world_name, success, message
    conversion_completed = pyqtSignal(int)  # total_successful
    log_message = pyqtSignal(str)  # message
    
    def __init__(self, worlds, jar_path, output_dir, target_version, java_path=None, add_suffix=False):
        super().__init__()
        self.worlds = worlds  # List of (world_name, world_path) tuples
        self.jar_path = jar_path
        self.output_dir = output_dir
        self.target_version = target_version
        self.java_path = java_path
        self.add_suffix = add_suffix  # Whether to add format suffix to output folder name
        self.stop_requested = False
        
    def run(self):
        successful = 0
        
        for world_name, world_path in self.worlds:
            if self.stop_requested:
                break
                
            # Determine output directory name
            if self.add_suffix:
                output_dir_name = f"{world_name}_{self.target_version.lower()}"
            else:
                output_dir_name = world_name
                
            target_dir = os.path.join(self.output_dir, output_dir_name)
            
            # Create target directory if it doesn't exist
            if not os.path.exists(target_dir):
                os.makedirs(target_dir)
            
            # Build command
            cmd = [
                self.java_path if self.java_path else "java", "-jar", self.jar_path,
                "-i", world_path,
                "-o", target_dir,
                "-f", self.target_version
            ]
            
            try:
                # Start process with piping to capture output in real time
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,  # Line buffered
                    universal_newlines=True,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                
                # Track progress
                last_percentage = 0
                errors = []
                
                # Use separate threads to read stdout and stderr to prevent deadlock
                def read_output(pipe, is_error):
                    nonlocal last_percentage
                    for line in iter(pipe.readline, ''):
                        line = line.strip()
                        if line:
                            # Check if it's a percentage
                            if '%' in line and not is_error:
                                try:
                                    percentage = float(line.replace('%', ''))
                                    if abs(percentage - last_percentage) >= 1.0:  # Only update on significant changes
                                        last_percentage = percentage
                                        self.progress_updated.emit(world_name, int(percentage))
                                except ValueError:
                                    pass
                            # Log all output
                            if is_error or not line.endswith('%'):  # Don't spam log with percentage updates
                                if "Missing" in line:  # Special handling for mapping errors
                                    errors.append(line)
                                    
                                self.log_message.emit(f"[{world_name}] {line}")
                
                # Start threads to read output
                stdout_thread = threading.Thread(target=read_output, args=(process.stdout, False))
                stderr_thread = threading.Thread(target=read_output, args=(process.stderr, True))
                stdout_thread.daemon = True
                stderr_thread.daemon = True
                stdout_thread.start()
                stderr_thread.start()
                
                # Wait for process to finish
                returncode = process.wait()
                
                # Wait for reader threads to finish
                stdout_thread.join(timeout=1.0)
                stderr_thread.join(timeout=1.0)
                
                if returncode == 0:
                    successful += 1
                    message = "Conversion successful"
                    if errors:
                        message += f" with {len(errors)} mapping warnings"
                    self.world_completed.emit(world_name, True, message)
                else:
                    error_summary = "\n".join(errors) if errors else "Unknown error"
                    self.world_completed.emit(world_name, False, f"Failed with exit code {returncode}: {error_summary}")
            
            except Exception as e:
                self.world_completed.emit(world_name, False, str(e))
        
        self.conversion_completed.emit(successful)
    
    def stop(self):
        """Request the thread to stop at the next opportunity"""
        self.stop_requested = True

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
                "JAVA_1_20", "JAVA_1_20_4", "JAVA_1_20_6",
                "JAVA_1_21", "JAVA_1_21_4", "JAVA_1_21_5"
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
        
        # Add documentation link
        docs_layout = QHBoxLayout()
        docs_label = QLabel("Documentation:")
        self.docs_link = QLabel('<a href="https://github.com/HiveGamesOSS/Chunker/blob/main/README.md">View Supported Versions</a>')
        self.docs_link.setOpenExternalLinks(True)
        docs_layout.addWidget(docs_label)
        docs_layout.addWidget(self.docs_link)
        docs_layout.addStretch(1)
        release_layout.addLayout(docs_layout)
        
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
        
        # Format version selector (ComboBox)
        self.format_version_combo = QComboBox()
        self.format_version_combo.currentTextChanged.connect(self.on_format_version_changed)
        conversion_layout.addWidget(self.format_version_combo, 3, 2)
        
        # Custom format entry (TextField)
        conversion_layout.addWidget(QLabel("Custom Format:"), 4, 0)
        self.custom_format_input = QLineEdit()
        self.custom_format_input.setPlaceholderText("e.g. JAVA_1_21_6 (only used with Custom option)")
        self.custom_format_input.setEnabled(False)
        conversion_layout.addWidget(self.custom_format_input, 4, 1, 1, 2)
        
        # Initialize format versions
        self.update_format_versions("Java")
        
        # Convert button
        self.convert_button = QPushButton("Start Conversion")
        self.convert_button.clicked.connect(self.start_conversion)
        self.convert_button.setEnabled(False)
        conversion_layout.addWidget(self.convert_button, 5, 0, 1, 3)
        
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
            # Add all predefined versions
            for version in self.formats[format_type]:
                self.format_version_combo.addItem(version)
                
            # Add Custom option at the end
            self.format_version_combo.addItem("Custom")
            
            # Select the latest version by default
            if self.format_version_combo.count() > 1:  # More than just Custom
                self.format_version_combo.setCurrentIndex(self.format_version_combo.count() - 2)  # Second to last (before Custom)
        
        # Reset custom format input
        self.custom_format_input.setEnabled(False)
        self.custom_format_input.clear()
    
    def on_format_version_changed(self, version):
        """Enable or disable custom format input based on selected version"""
        self.custom_format_input.setEnabled(version == "Custom")
    
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
            self.convert_button.setEnabled(bool(self.jar_path and self.selected_input_dir and self.selected_output_dir))
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
        
        if target_version == "Custom":
            target_version = self.custom_format_input.text().strip()
            if not target_version:
                QMessageBox.warning(self, "Warning", "Please enter a custom format version")
                return
        
        if not target_version:
            QMessageBox.warning(self, "Warning", "Please select a target format version")
            return
        
        # Check Java version compatibility
        if not self.check_java_version():
            return
        
        # Lock all input controls during conversion
        self.set_controls_enabled(False)
        
        # Start conversion
        self.update_status_list("Starting conversion process...")
        self.status_label.setText("Status: Converting...")
        self.convert_button.setEnabled(False)
        self.convert_button.setText("Converting...")
        
        # Process all worlds in input directory
        input_dirs = [d for d in os.listdir(self.selected_input_dir) 
                     if os.path.isdir(os.path.join(self.selected_input_dir, d))]
        
        # Check if we have any potential world directories
        if not input_dirs:
            QMessageBox.warning(self, "Warning", "No directories found in the input directory")
            self.convert_button.setEnabled(True)
            self.convert_button.setText("Start Conversion")
            self.status_label.setText("Status: No worlds found")
            self.set_controls_enabled(True)  # Re-enable controls
            return
        
        # Prepare worlds for conversion
        worlds = []
        for d in input_dirs:
            full_path = os.path.join(self.selected_input_dir, d)
            if self.is_minecraft_world(full_path):
                worlds.append((d, full_path))
            else:
                self.update_status_list(f"Skipping {d} - doesn't look like a Minecraft world")
        
        if not worlds:
            QMessageBox.warning(self, "Warning", "No valid Minecraft worlds found in the input directory")
            self.convert_button.setEnabled(True)
            self.convert_button.setText("Start Conversion")
            self.status_label.setText("Status: No valid worlds found")
            self.set_controls_enabled(True)  # Re-enable controls
            return
        
        # Create progress widget with responsive layout
        self.progress_widget = QWidget()
        progress_layout = QVBoxLayout(self.progress_widget)
        progress_layout.setContentsMargins(5, 5, 5, 5)
        
        # Create a progress bar for overall progress
        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        self.overall_progress.setValue(0)
        self.overall_progress.setFormat("Overall Progress: %p%")
        # Make progress bar stretch horizontally
        self.overall_progress.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        progress_layout.addWidget(self.overall_progress)
        
        # Create a progress bar for current world progress
        self.world_progress = QProgressBar()
        self.world_progress.setRange(0, 100)
        self.world_progress.setValue(0)
        self.world_progress.setFormat("Current World: %p%")
        # Make progress bar stretch horizontally
        self.world_progress.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        progress_layout.addWidget(self.world_progress)
        
        # Show the cancel button
        self.cancel_button = QPushButton("Cancel Conversion")
        self.cancel_button.clicked.connect(self.cancel_conversion)
        progress_layout.addWidget(self.cancel_button)
        
        # Add the progress widget to the layout between conversion and status sections
        main_layout = self.centralWidget().layout()
        
        # Find the indexes of the conversion and status group boxes
        conversion_idx = -1
        status_idx = -1
        for i in range(main_layout.count()):
            widget = main_layout.itemAt(i).widget()
            if isinstance(widget, QGroupBox):
                if widget.title() == "Batch Conversion":
                    conversion_idx = i
                elif widget.title() == "Status":
                    status_idx = i
        
        # Insert progress widget between conversion and status
        if conversion_idx >= 0 and status_idx >= 0:
            main_layout.insertWidget(status_idx, self.progress_widget)
        else:
            # Fallback if we couldn't find the sections
            main_layout.addWidget(self.progress_widget)
        
        # Start conversion thread (with add_suffix=False to prevent format suffixes)
        self.conversion_thread = ConversionThread(
            worlds, 
            self.jar_path, 
            self.selected_output_dir, 
            target_version, 
            self.custom_java_path, 
            add_suffix=False  # Don't add format suffixes to world folders
        )
        
        self.conversion_thread.progress_updated.connect(self.update_conversion_progress)
        self.conversion_thread.world_completed.connect(self.on_world_completed)
        self.conversion_thread.conversion_completed.connect(self.on_conversion_completed)
        self.conversion_thread.log_message.connect(self.update_status_list)
        
        self.update_status_list(f"Starting conversion of {len(worlds)} worlds to {target_version}")
        self.current_world_index = 0
        self.total_worlds = len(worlds)
        self.conversion_thread.start()
    
    def cancel_conversion(self):
        """Cancel the running conversion process"""
        if hasattr(self, 'conversion_thread') and self.conversion_thread.isRunning():
            reply = QMessageBox.question(
                self, 
                'Confirm Cancellation',
                "Are you sure you want to cancel the conversion process?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
                QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                self.update_status_list("Cancelling conversion process...")
                self.conversion_thread.stop()
                self.conversion_thread.wait(1000)  # Give thread 1 sec to clean up
                self.on_conversion_completed(0, cancelled=True)
    
    def update_conversion_progress(self, world_name, percentage):
        """Update conversion progress for the current world"""
        self.world_progress.setValue(percentage)
        self.world_progress.setFormat(f"{world_name}: {percentage}%")
        
        # Update overall progress
        if self.total_worlds > 0:
            overall = int((self.current_world_index * 100 + percentage) / self.total_worlds)
            self.overall_progress.setValue(overall)
            
        self.status_label.setText(f"Status: Converting {world_name} ({percentage}%)")
    
    def on_world_completed(self, world_name, success, message):
        """Handle completion of a single world conversion"""
        if success:
            self.update_status_list(f"✓ Successfully converted {world_name}: {message}")
        else:
            self.update_status_list(f"✗ Failed to convert {world_name}: {message}")
            
        self.current_world_index += 1
        
    def on_conversion_completed(self, total_successful, cancelled=False):
        """Handle completion of all conversions"""
        # Clean up progress bars and cancel button
        if hasattr(self, 'progress_widget'):
            self.progress_widget.setParent(None)
            self.progress_widget.deleteLater()
            delattr(self, 'progress_widget')
        
        self.convert_button.setEnabled(True)
        self.convert_button.setText("Start Conversion")
        
        # Re-enable controls
        self.set_controls_enabled(True)
        
        if cancelled:
            self.status_label.setText("Status: Conversion cancelled")
            self.update_status_list("Conversion process was cancelled")
        elif total_successful > 0:
            self.status_label.setText(f"Status: Conversion complete ({total_successful} of {self.total_worlds} conversions)")
            self.update_status_list("Conversion process completed")
        else:
            self.status_label.setText("Status: No worlds converted successfully")
            self.update_status_list("No worlds were converted successfully")
    
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
    
    def set_controls_enabled(self, enabled):
        """Enable or disable all input controls"""
        # JAR selection controls
        self.release_combo.setEnabled(enabled)
        self.download_button.setEnabled(enabled and self.selected_version is not None)
        self.browse_jar_button.setEnabled(enabled)
        self.refresh_button.setEnabled(enabled)
        
        # Java and directory selection
        self.browse_java_button.setEnabled(enabled)
        self.browse_input_button.setEnabled(enabled)
        self.browse_output_button.setEnabled(enabled)
        
        # Format selection
        self.format_type_combo.setEnabled(enabled)
        self.format_version_combo.setEnabled(enabled)
        self.custom_format_input.setEnabled(enabled and self.format_version_combo.currentText() == "Custom")
        
        # Convert button is managed separately

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ChunkerBatchConverter()
    window.show()
    sys.exit(app.exec())