FROM confluentinc/cp-kafka-connect:7.5.0

# Install JDBC Sink connector
RUN confluent-hub install --no-prompt confluentinc/kafka-connect-jdbc:10.7.4

# Install Avro converter
RUN confluent-hub install --no-prompt confluentinc/kafka-connect-avro-converter:7.5.0

# Download PostgreSQL JDBC driver (instead of COPY)
RUN curl -o /usr/share/java/postgresql-42.7.1.jar \
    https://jdbc.postgresql.org/download/postgresql-42.7.1.jar

# Set plugin path
ENV CONNECT_PLUGIN_PATH="/usr/share/java,/usr/share/confluent-hub-components"