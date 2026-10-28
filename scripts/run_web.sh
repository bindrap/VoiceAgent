#!/bin/bash
# Helper to launch the VoiceAgent Web UI

# 1. Restart the whisper server on 8085 (leave anything on 8080 alone)
fuser -k 8085/tcp >/dev/null 2>&1

echo "Starting Whisper server on port 8085..."
# We use nohup and redirect output to a log file so it doesn't block the terminal
nohup /home/parteek/Clone/whisper.cpp/build-vulkan/bin/whisper-server \
    -m /home/parteek/Clone/whisper.cpp/models/ggml-large-v3-turbo.bin \
    --port 8085 \
    --convert > whisper_server.log 2>&1 &

echo "Waiting for Whisper server to initialize..."
sleep 5

# 2. Launch VoiceAgent in web mode
echo "Launching VoiceAgent Web UI on http://127.0.0.1:8090"
export PYTHONPATH=$PYTHONPATH:"$(pwd)/.."
python3 -m voiceagent --web
