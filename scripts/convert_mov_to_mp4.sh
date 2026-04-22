#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: scripts/convert_mov_to_mp4.sh <input.mov> [output.mp4]"
  echo "Example: scripts/convert_mov_to_mp4.sh ~/Desktop/video.mov"
  echo "Example: scripts/convert_mov_to_mp4.sh input.mov output.mp4"
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required but was not found in PATH."
  echo "Install it first, then run this script again."
  exit 1
fi

INPUT_FILE="$1"
OUTPUT_FILE="${2:-${INPUT_FILE%.*}.mp4}"

if [[ ! -f "$INPUT_FILE" ]]; then
  echo "Input file not found: $INPUT_FILE"
  exit 1
fi

if [[ ! -r "$INPUT_FILE" ]]; then
  echo "Input file is not readable: $INPUT_FILE"
  echo "On macOS, grant your terminal app access to Desktop/Documents/Downloads or Full Disk Access."
  exit 1
fi

if [[ "$INPUT_FILE" == "$OUTPUT_FILE" ]]; then
  echo "Input and output paths must be different."
  exit 1
fi

if [[ -e "$OUTPUT_FILE" ]]; then
  echo "Output file already exists: $OUTPUT_FILE"
  echo "Choose a different output path or remove the existing file."
  exit 1
fi

OUTPUT_DIR="$(dirname "$OUTPUT_FILE")"

if [[ ! -d "$OUTPUT_DIR" || ! -w "$OUTPUT_DIR" ]]; then
  echo "Output directory is not writable: $OUTPUT_DIR"
  echo "On macOS, grant your terminal app access to that folder or choose a different output path."
  exit 1
fi

ffmpeg \
  -i "$INPUT_FILE" \
  -c:v libx264 \
  -crf 18 \
  -preset medium \
  -pix_fmt yuv420p \
  -c:a aac \
  -b:a 192k \
  -movflags +faststart \
  "$OUTPUT_FILE"

echo "Created: $OUTPUT_FILE"
