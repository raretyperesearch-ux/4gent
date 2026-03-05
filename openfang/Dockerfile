FROM debian:bookworm-slim

# Install dependencies
RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install OpenFang binary
RUN curl -fsSL https://openfang.sh/install | sh

# Add to PATH
ENV PATH="/root/.openfang/bin:${PATH}"

# Create config directory
RUN mkdir -p /root/.openfang

# Copy config
COPY openfang.toml /root/.openfang/config.toml

EXPOSE 4200

CMD ["/root/.openfang/bin/openfang", "start"]
