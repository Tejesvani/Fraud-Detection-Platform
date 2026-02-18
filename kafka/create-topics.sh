#!/bin/bash
# Permission to execute: chmod +x create-topics.sh
# Run create-topics.sh inside kafka: ./create-topics.sh
set -e

BOOTSTRAP_SERVER="${KAFKA_BOOTSTRAP_SERVERS:-localhost:9092}"
TOPIC_TRANSACTIONS="${KAFKA_TOPIC_TRANSACTIONS:-transactions}"
TOPIC_RISK_SCORES="${KAFKA_TOPIC_RISK_SCORES:-risk_scores}"
TOPIC_ALERTS="${KAFKA_TOPIC_ALERTS:-alerts}"

echo "Creating Kafka topics..."

docker exec kafka-prod kafka-topics \
  --bootstrap-server $BOOTSTRAP_SERVER \
  --create \
  --if-not-exists \
  --topic $TOPIC_TRANSACTIONS \
  --partitions 3 \
  --replication-factor 1

docker exec kafka-prod kafka-topics \
  --bootstrap-server $BOOTSTRAP_SERVER \
  --create \
  --if-not-exists \
  --topic $TOPIC_RISK_SCORES \
  --partitions 3 \
  --replication-factor 1

docker exec kafka-prod kafka-topics \
  --bootstrap-server $BOOTSTRAP_SERVER \
  --create \
  --if-not-exists \
  --topic $TOPIC_ALERTS \
  --partitions 1 \
  --replication-factor 1

echo "Kafka topics created successfully... "