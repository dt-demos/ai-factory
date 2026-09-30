#!/bin/bash
set -e

# Generate config.yml files from template.
# CONFIG_TYPE: "build" (NVIDIA cloud API), "local" (local dev), "brev" (custom NIM endpoints)
CONFIG_TYPE="${CONFIG_TYPE:-brev}"
echo "Generating config for type: ${CONFIG_TYPE}"
python update_config.py "${CONFIG_TYPE}"

exec streamlit run app.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
