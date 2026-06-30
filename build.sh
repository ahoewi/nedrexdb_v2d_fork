#!/bin/bash

# Save environment variables for cron jobs
declare -x > /app/nedrexdb/container_env.sh

setup_db() {
    local db_type=$1
    local config_file=".$db_type"_config.toml
    local lock_file="/tmp/nedrexdb_build_${db_type}.lock"

    if [ -f "$lock_file" ]; then
        local existing_pid
        existing_pid=$(cat "$lock_file" 2>/dev/null)
        if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') | WARNING |  build.sh - ${db_type} build already in progress (PID $existing_pid), skipping."
            return 1
        fi
        echo "$(date '+%Y-%m-%d %H:%M:%S') | WARNING |  build.sh - Removing stale lock for ${db_type} (PID $existing_pid no longer running)"
    fi
    echo $$ > "$lock_file"
    trap "rm -f $lock_file" EXIT

    if [[ "$LOG_LEVEL" == "INFO" || "$LOG_LEVEL" == "DEBUG" ]]; then echo "$(date '+%Y-%m-%d %H:%M:%S') | INFO |  build.sh - Starting setup of $db_type DB"; fi

    # Handle DB updates
    if [[ "$SKIP_UPDATE" == "1" ]]; then
        if [[ "$CREATE_EMBEDDINGS" == "1" ]]; then
          ./build.py embed-only --conf "$config_file"
        else
          ./build.py restart-live --conf "$config_file"
        fi
    else
        local build_args=(update --conf "$config_file")

        if [[ "$FORCE_REBUILD" == "1" ]]; then
          build_args+=(--rebuild)
        fi

        if [[ "$DOWNLOAD_ON_STARTUP" == "1" || "$FORCE_REBUILD" == "1" ]]; then

          # update incl. metadata when setting download flag
          if [[ "$db_type" == "licensed" ]]; then
              build_args+=(--download)

           # when only building open db, download flag must be set
          elif [[ "$db_type" == "open" ]]; then
              if [[ "$SKIP_LICENSED" == "1" ]]; then
                build_args+=(--download)
              else
                build_args+=(--version_update .licensed_config.toml)
              fi

          fi
        # set versions anyways when no download
        else
          build_args+=(--version_update true)

        fi

        if [[ "$CREATE_EMBEDDINGS" == "1" ]]; then
          build_args+=(--create_embeddings)
        fi
        if [[ "$LOG_LEVEL" == "DEBUG" ]]; then echo "$(date '+%Y-%m-%d %H:%M:%S') | DEBUG |  build.sh - Running build with command: ./build.py ${build_args[@]}"; fi
        ./build.py "${build_args[@]}"
    fi

    # Clean volumes if not skipped
    if [[ "$SKIP_CLEAN" != "1" ]]; then
        if [[ "$LOG_LEVEL" == "INFO" || "$LOG_LEVEL" == "DEBUG" ]]; then echo "$(date '+%Y-%m-%d %H:%M:%S') | INFO |  build.sh -Cleaning unused nedrex volumes"; fi
        ./clean_volumes.sh "$db_type"
    else
        if [[ "$LOG_LEVEL" == "DEBUG" ]]; then echo "$(date '+%Y-%m-%d %H:%M:%S') | DEBUG |  build.sh - Skipping clean"; fi
    fi

    if [[ "$LOG_LEVEL" == "INFO" || "$LOG_LEVEL" == "DEBUG" ]]; then echo "$(date '+%Y-%m-%d %H:%M:%S') | INFO |  build.sh - Finished setup of $db_type DB"; fi
}

# Setup licensed DB if not skipped
[[ "$SKIP_LICENSED" != "1" ]] &&  setup_db licensed
# Setup open DB if not skipped
[[ "$SKIP_OPEN" != "1" ]] && setup_db open