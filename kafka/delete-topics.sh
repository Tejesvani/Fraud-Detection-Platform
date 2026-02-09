#!/bin/bash
set -e

BOOTSTRAP_SERVER="localhost:9092"

echo "Deleting Kafka topics..."

for topic in transactions risk_scores alerts; do
  docker exec kafka kafka-topics \
    --bootstrap-server $BOOTSTRAP_SERVER \
    --delete \
    --topic $topic || true
done

echo "Topics deleted"
