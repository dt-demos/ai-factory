#!/bin/bash

# Script to start Docker containers using specified config file
# Usage: ./start-otel.sh [config.yaml|config-nim.yaml]
# Default: config.yaml 

# Get config file from command line argument or use default
CONFIG_FILE=${1:-config.yaml}

# Validate config file argument
case "$CONFIG_FILE" in
    config.yaml|config-nim.yaml)
        echo "Using configuration: $CONFIG_FILE"
        ;;
    *)
        echo "❌ Error: Invalid configuration file: $CONFIG_FILE"
        echo "Valid options: config.yaml, config-nim.yaml"
        exit 1
        ;;
esac

# Function to start a container
start_container() {
    local container_name=otel-collector
    local config_file=$1
    
    # Check if container exists and is running
    if docker ps -q -f name="^${container_name}$" | grep -q .; then
        echo "✅ Container ${container_name} is already running"
        echo ""
        return
    fi
    
    # Check if container exists but is stopped
    if docker ps -aq -f name="^${container_name}$" | grep -q .; then
        echo "▶️  Starting existing container: ${container_name}"
        docker start "${container_name}"
        echo ""
        return
    fi
    
    # Container doesn't exist, create and start it based on config type
    echo "🔄 Starting dynatrace-otel-collector container with ${config_file}..."
    
    case "$config_file" in
        config.yaml)
            echo "Starting with config.yaml..."
            echo "DT_BASE_URL = $DT_BASE_URL"
            echo "Container = "
            docker run -d --rm -v "$(pwd)"/${config_file}:/etc/otelcol/config.yaml \
            --name ${container_name} \
            -e DT_BASE_URL=$DT_BASE_URL \
            -e DT_API_TOKEN=$DT_API_TOKEN \
            -p 4317:4317 \
            -p 4318:4318 \
            dynatrace/dynatrace-otel-collector:latest
            ;;
        config-nim.yaml)
            echo "Starting with config-nim.yaml..."
            echo "DT_BASE_URL = $DT_BASE_URL"
            echo "NIM_HOST = $NIM_HOST"
            echo "Container = "
            docker run -d --rm -v "$(pwd)"/${config_file}:/etc/otelcol/config.yaml \
            --name ${container_name} \
            -e DT_BASE_URL=$DT_BASE_URL \
            -e DT_API_TOKEN=$DT_API_TOKEN \
            -p 4317:4317 \
            -p 4318:4318 \
            -e NIM1_TARGET=nim-host:8001   \
            -e NIM2_TARGET=nim-host:8002   \
            -e NIM3_TARGET=nim-host:8003   \
            --add-host "nim-host:$NIM_HOST" \
            dynatrace/dynatrace-otel-collector:latest
            ;;
    esac
    
    echo ""
}
    
# Main logic
echo "🔄 Starting otel container..."
start_container "$CONFIG_FILE"
echo "✅ otel containers processed"
echo ""
echo "✅ Showing list of containers"
echo ""
docker ps