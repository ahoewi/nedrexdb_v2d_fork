#!/usr/bin/bash

if [ -f /app/nedrexdb/container_env.sh ]; then
  source /app/nedrexdb/container_env.sh
fi

export PATH="/opt/conda/bin:${PATH}"
echo $PATH
if [[ "$AUTOUPDATE" != "0" ]]; then
  export SKIP_UPDATE=0
fi
./build.sh
