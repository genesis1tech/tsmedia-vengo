#!/usr/bin/env bash
set -euo pipefail
DB=tsv6
TABLE=scans_v2

if ! aws glue get-database --name "$DB" >/dev/null 2>&1; then
  aws glue create-database --database-input "Name=$DB,Description=TSV6 analytics tables"
  echo "Created Glue database $DB."
fi

if aws glue get-table --database-name "$DB" --name "$TABLE" >/dev/null 2>&1; then
  echo "Table $DB.$TABLE exists, updating..."
  aws glue update-table --database-name "$DB" --table-input "file://infra/aws/v2/glue_table_schema.json"
else
  echo "Creating $DB.$TABLE..."
  aws glue create-table  --database-name "$DB" --table-input "file://infra/aws/v2/glue_table_schema.json"
fi
echo "Done."
