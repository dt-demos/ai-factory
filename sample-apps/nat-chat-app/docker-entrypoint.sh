#!/bin/bash
set -e

# Generate config.yml files from template.
# CONFIG_TYPE: "cloud" (NVIDIA cloud API endpoints) or "k8s" (NIM services in the k8s cluster)
CONFIG_TYPE="${CONFIG_TYPE:-cloud}"
echo "Generating config for type: ${CONFIG_TYPE}"
python update_config.py "${CONFIG_TYPE}"

exec streamlit run app.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
