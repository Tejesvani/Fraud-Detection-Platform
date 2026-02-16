#!/bin/bash
set -e

BOOTSTRAP_SERVER="${KAFKA_BOOTSTRAP_SERVERS:-localhost:9092}"
TOPIC_TRANSACTIONS="${KAFKA_TOPIC_TRANSACTIONS:-transactions}"
TOPIC_RISK_SCORES="${KAFKA_TOPIC_RISK_SCORES:-risk_scores}"
TOPIC_ALERTS="${KAFKA_TOPIC_ALERTS:-alerts}"

echo "Deleting Kafka topics..."

for topic in $TOPIC_TRANSACTIONS $TOPIC_RISK_SCORES $TOPIC_ALERTS; do
  docker exec kafka kafka-topics \
    --bootstrap-server $BOOTSTRAP_SERVER \
    --delete \
    --topic $topic || true
done

echo "Topics deleted"
