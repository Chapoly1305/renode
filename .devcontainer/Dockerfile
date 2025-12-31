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
    # Shell and editor tools
    zsh \
    vim \
    nano \
    less \
    htop \
    tree \
    jq \
    ripgrep \
    fd-find \
    fzf \
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
    && useradd --uid $USER_UID --gid $USER_GID -m $USERNAME -s /bin/zsh \
    && echo $USERNAME ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME

# Set environment variables
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1

# Switch to vscode user
USER $USERNAME
WORKDIR /home/$USERNAME

# Install oh-my-zsh with plugins
RUN sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended \
    && git clone https://github.com/zsh-users/zsh-autosuggestions ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-autosuggestions \
    && git clone https://github.com/zsh-users/zsh-syntax-highlighting ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting \
    && git clone https://github.com/zsh-users/zsh-completions ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-completions

# Configure zsh with smart tab completion
RUN sed -i 's/plugins=(git)/plugins=(git zsh-autosuggestions zsh-syntax-highlighting zsh-completions dotnet docker)/' ~/.zshrc \
    && echo '' >> ~/.zshrc \
    && echo '# Smart tab completion' >> ~/.zshrc \
    && echo 'zstyle ":completion:*" menu select' >> ~/.zshrc \
    && echo 'zstyle ":completion:*" matcher-list "m:{a-zA-Z}={A-Za-z}"' >> ~/.zshrc \
    && echo 'bindkey "^[[Z" reverse-menu-complete' >> ~/.zshrc \
    && echo '' >> ~/.zshrc \
    && echo '# fzf integration' >> ~/.zshrc \
    && echo 'export FZF_DEFAULT_OPTS="--height 40% --layout=reverse --border"' >> ~/.zshrc \
    && echo 'source <(fzf --zsh 2>/dev/null) || true' >> ~/.zshrc

WORKDIR /workspaces/renode

# Set default shell to zsh
SHELL ["/bin/zsh", "-c"]
