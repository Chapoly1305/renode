# Renode Development Container
# Based on Ubuntu 22.04 with .NET 8.0 SDK

FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive

# Install .NET SDK 8.0
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    ca-certificates \
    && wget https://packages.microsoft.com/config/ubuntu/22.04/packages-microsoft-prod.deb -O packages-microsoft-prod.deb \
    && dpkg -i packages-microsoft-prod.deb \
    && rm packages-microsoft-prod.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends dotnet-sdk-8.0 \
    && rm -rf /var/lib/apt/lists/*

# Install essential build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Build tools
    build-essential \
    cmake \
    ninja-build \
    automake \
    autoconf \
    libtool \
    pkg-config \
    # Git and version control
    git \
    git-lfs \
    # Python
    python3 \
    python3-pip \
    python3-venv \
    # GTK and GUI dependencies
    libgtk2.0-0 \
    libgtk2.0-dev \
    gtk-sharp2 \
    # System libraries
    libc6-dev \
    libicu-dev \
    zlib1g-dev \
    libssl-dev \
    # Utilities
    curl \
    unzip \
    zip \
    xz-utils \
    screen \
    uml-utilities \
    policykit-1 \
    # Additional tools
    sudo \
    locales \
    && rm -rf /var/lib/apt/lists/*

# Set locale
RUN locale-gen en_US.UTF-8
ENV LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8

# Install GNU Parallel for faster builds
RUN apt-get update && apt-get install -y --no-install-recommends parallel \
    && rm -rf /var/lib/apt/lists/*

# Create vscode user
ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=$USER_UID

RUN groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID -m $USERNAME \
    && echo $USERNAME ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME

# Set environment variables
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1

# Switch to vscode user
USER $USERNAME
WORKDIR /workspaces/renode

# Set default shell to bash
SHELL ["/bin/bash", "-c"]
