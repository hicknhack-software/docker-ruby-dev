# syntax=docker/dockerfile:1

ARG BASE_IMAGE_TAG=latest

FROM ruby:${BASE_IMAGE_TAG}

# Explicitely define locale
# as advised in https://github.com/docker-library/docs/blob/master/ruby/content.md#encoding
ENV LANG="C.UTF-8"

ARG RUBYGEMS_VERSION_ARG="" \
    BUNDLER_VERSION_ARG="" \
    DEBIAN_FRONTEND=noninteractive

# Define dependencies base versions
# Note: NodeJS is capped to 14.x on Jessie and 16.x on Stretch (due to `libc` requirements)
ENV NODE_VERSION="20" \
    GOSU_VERSION="1.17"

# Define some default variables
ENV PORT="5000" \
    BUNDLE_PATH="/bundle" \
    BUNDLE_BIN="/bundle/bin" \
    BUNDLE_APP_CONFIG="/bundle" \
    GEM_HOME="/bundle/global" \
    PATH="/bundle/bin:/bundle/global/bin:${PATH}" \
    HISTFILE="/config/.bash_history" \
    GIT_COMMITTER_NAME="Just some fake name to be able to git-clone" \
    GIT_COMMITTER_EMAIL="whatever@this-user-is-not-supposed-to-git-push.anyway" \
    DISABLE_SPRING="true"

# Install dependencies
RUN <<INSTALL_DEPENDENCIES
  set -eux
  osType="$(sed -n 's|^ID=||p' /etc/os-release)"

  case "${osType}" in
    alpine) \
      alpineMajorVersion=$(sed -nr 's/^VERSION_ID=(\d+\.\d+).*/\1/p' /etc/os-release)
      \
      # Use `libpq-dev` (~20MB) rather than `postgresql-dev` (~200MB) if available
      # (the former was extracted from the latter in Alpine 3.15)
      case ${alpineMajorVersion} in
        3.3|3.4|3.5|3.6|3.7|3.8|3.9|3.10|3.11|3.12|3.13|3.14)
          libpqPackage="postgresql-dev"
          ;;
        3.15|*)
          libpqPackage="libpq-dev"
          ;;
      esac

      apk add --no-cache \
        alpine-sdk \
        openssh \
        curl \
        jq \
        nano \
        nodejs \
        postgresql \
        vim \
        yarn \
        ${libpqPackage}

    ;;

    debian|ubuntu) \
      # Fix Jessie & Stretch APT sources (they have been moved to http://archive.debian.org)
      if [ -f /etc/apt/sources.list ]; then
        sed -i -r \
          -e '/(jessie|stretch)[-\/]updates/d' \
          -e 's|http://(deb\|httpredir).debian.org/debian (jessie\|stretch)|http://archive.debian.org/debian \2|' \
          /etc/apt/sources.list
      fi
      \
      # Detect Debian version
      apt-get -qq --yes update -o=Dpkg::Use-Pty=0
      apt-get -qq --yes install -o=Dpkg::Use-Pty=0 --no-install-recommends --no-install-suggests \
        apt-transport-https \
        lsb-release

      debianReleaseCodename=$(lsb_release -cs)
      \
      # Fix LetsEncrypt expired CA on older Debian releases
      case ${debianReleaseCodename} in
        jessie|buster|stretch)
          apt-get -qq --yes install -o=Dpkg::Use-Pty=0 --no-install-recommends --no-install-suggests \
            ca-certificates \
            curl \
            $([ "${debianReleaseCodename}" = "jessie" ] && echo "libssl1.0.0")

          sed -i 's|mozilla/DST_Root_CA_X3.crt|!mozilla/DST_Root_CA_X3.crt|g' /etc/ca-certificates.conf
          update-ca-certificates
        ;;
      esac
      \
      # Add PostgreSQL APT repository
      curl -sSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor > /etc/apt/trusted.gpg.d/apt.postgresql.org.gpg
      case ${debianReleaseCodename} in
        jessie|stretch)
          echo "deb https://apt-archive.postgresql.org/pub/repos/apt ${debianReleaseCodename}-pgdg-archive main"
          ;;
        *)
          echo "deb https://apt.postgresql.org/pub/repos/apt/ ${debianReleaseCodename}-pgdg main"
          ;;
      esac > /etc/apt/sources.list.d/pgdg.list
      \
      # Add NodeJS APT repository
      case ${debianReleaseCodename} in
        jessie)
          curl -fsSL https://deb.nodesource.com/setup_14.x
          ;;
        stretch)
          curl -fsSL https://deb.nodesource.com/setup_16.x
          ;;
        *)
          curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x
          ;;
      esac | bash
      \
      # Install everything
      apt-get -qq --yes update -o=Dpkg::Use-Pty=0
      apt-get -qq --yes install -o=Dpkg::Use-Pty=0 --no-install-recommends --no-install-suggests \
        jq \
        nano \
        nodejs \
        postgresql-client \
        vim

      \
      # Cleanup
      rm -rf /var/lib/apt/lists/*
      \
      # Install Yarn (via NPM)
      npm install --global yarn
      \
      # Install Heroku CLI (standalone tarball)
      curl -sSL https://cli-assets.heroku.com/install.sh | sh
  esac
INSTALL_DEPENDENCIES

# Install `gosu`
ARG TARGETARCH # provided by Docker multi-platform support: https://docs.docker.com/build/guide/multi-platform
RUN <<INSTALL_GOSU
  set -eux
  osType="$(sed -n 's|^ID=||p' /etc/os-release)"
  export GNUPGHOME="$(mktemp -d)"
  \
  # Install GPG on Alpine (for signature verification)
  [ "${osType}" = "alpine" ] && apk add --no-cache --virtual .gosu-deps gnupg || :
  \
  # Fetch author public key
  for keyserver in $(shuf -e keyserver.ubuntu.com keys.openpgp.org keys.mailvelope.com); do
    gpg --batch --no-tty --keyserver "${keyserver}" --recv-keys "B42F6819007F00F88E364FD4036A9C25BF357DD4" && break || :
  done
  \
  # Download binary
  curl -sSL -o /usr/local/bin/gosu "https://github.com/tianon/gosu/releases/download/${GOSU_VERSION}/gosu-${TARGETARCH}"
  chmod +x /usr/local/bin/gosu
  \
  # Verify binary signature
  curl -sSL "https://github.com/tianon/gosu/releases/download/${GOSU_VERSION}/gosu-${TARGETARCH}.asc" \
    | gpg --batch --verify - /usr/local/bin/gosu
  \
  # Cleanup
  command -v gpgconf && gpgconf --kill all || :
  rm -rf "${GNUPGHOME}"
  unset -v GNUPGHOME
  [ "${osType}" = "alpine" ] && apk del --no-network .gosu-deps || :
INSTALL_GOSU

# Install GEM dependencies
# Note: we still need Bundler 1.x because Bundler auto-switches to it when it encounters a Gemfile.lock with BUNDLED WITH 1.x
RUN <<INSTALL_BUNDLERS
  gem update --system ${RUBYGEMS_VERSION_ARG}
  gem install bundler${BUNDLER_VERSION_ARG:+:${BUNDLER_VERSION_ARG}}
  gem install bundler:1.17.3
INSTALL_BUNDLERS

# Add dot files to the home directory skeleton (they persist IRB/Pry/Rails console history, configure Yarn, etc…)
COPY dotfiles/* /etc/skel/

# Create expected mount points
RUN mkdir -p /app /bundle /config

# Configure the main working directory.
WORKDIR /app

# Expose listening port to the Docker host, so we can access it from the outside.
EXPOSE ${PORT}

# Use entrypoints that switch to unprivileged user, install foreman, install dependencies (bundler & yarn), and fix a Rails server issue
COPY entrypoints/* /usr/local/bin/
ENTRYPOINT ["gosu-entrypoint", "foreman-entrypoint", "bundler-entrypoint", "yarn-entrypoint", "rails-entrypoint"]

# The main command to run when the container starts is to start whatever the Procfile defines
CMD ["foreman", "start", "-m", "all=1,release=0"]
