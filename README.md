# Chunker Batch Converter

A GUI application for batch converting Minecraft worlds between Java and Bedrock editions using the [Chunker](https://github.com/HiveGamesOSS/Chunker) tool.

![image](https://github.com/user-attachments/assets/dd70c335-52df-45c1-b5c1-9d7c81e4b3b5)

## Features

- Download and manage Chunker CLI JAR files directly from GitHub releases
- Batch convert multiple Minecraft worlds at once
- Support for both Java to Bedrock and Bedrock to Java conversions

## Requirements

- Python 3.9 or higher
- PyQt6
- Java 17 or higher *(required for Chunker)*

## Installation

1. Install Python dependencies:
```
pip install PyQt6 requests
```

2. Run the application:
```
python main.py
```

## Usage

### Input Directory Structure

The input directory should contain one or more Minecraft world folders. Each world folder should have the following structure:

```
Input folder
├── world1
│   ├── level.dat
│   └── ... (other world files)
├── world2
│   ├── level.dat
│   └── ... (other world files)
└── ...
```

### Step-by-Step Usage

1. Download or select a Chunker CLI JAR file
   - Click "Download Selected Version" to get the latest version from GitHub
   - Or click "Browse for JAR" if you already have the JAR file
2. Select a Java executable (optional)
   - If you have multiple Java versions, click "Select Java" to choose Java 17+
3. Select input and output directories
   - Input directory should contain Minecraft world folders
   - Output directory is where converted worlds will be saved
4. Choose target format
   - Select Java or Bedrock as the target edition
   - Select the specific game version
   - Or choose "Custom" to enter a custom format version
5. Click "Start Conversion" to begin the batch conversion process

### Supported Formats

For the latest list of supported formats, check the [Chunker Repository](https://github.com/HiveGamesOSS/Chunker/blob/main/README.md).

Common formats include:
- Java: JAVA_1_21_5 for Minecraft Java 1.21.5
- Bedrock: BEDROCK_1_21_70, for Minecraft Bedrock 1.21.70
