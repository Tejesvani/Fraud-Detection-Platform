#!/bin/bash
set -e

BOOTSTRAP_SERVER="localhost:9092"

echo "Creating Kafka topics..."

docker exec kafka kafka-topics \
  --bootstrap-server $BOOTSTRAP_SERVER \
  --create \
  --if-not-exists \
  --topic transactions \
  --partitions 3 \
  --replication-factor 1

docker exec kafka kafka-topics \
  --bootstrap-server $BOOTSTRAP_SERVER \
  --create \
  --if-not-exists \
  --topic risk_scores \
  --partitions 3 \
  --replication-factor 1

docker exec kafka kafka-topics \
  --bootstrap-server $BOOTSTRAP_SERVER \
  --create \
  --if-not-exists \
  --topic alerts \
  --partitions 1 \
  --replication-factor 1

echo "Kafka topics created successfully... "
