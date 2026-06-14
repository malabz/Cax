FROM ubuntu:22.04 AS ramax-builder

ARG DEBIAN_FRONTEND=noninteractive
ARG TARGETARCH
ARG BUILD_JOBS=4
ARG RAMAX_REPOSITORY=https://github.com/pinglu-zhang/RaMAx.git
ARG RAMAX_REF=master

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN case "${TARGETARCH:-amd64}" in \
      amd64) ;; \
      *) echo "CAX Docker builds currently support linux/amd64 only; got TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      cmake \
      g++-12 \
      gcc-12 \
      git \
      libcurl4-openssl-dev \
      libhdf5-dev \
      libtbb-dev \
      make \
      pkg-config \
      zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

ENV CC=/usr/bin/gcc-12
ENV CXX=/usr/bin/g++-12

WORKDIR /src

RUN git clone --depth 1 --branch "${RAMAX_REF}" "${RAMAX_REPOSITORY}" ramax \
    || { \
         rm -rf ramax; \
         git clone "${RAMAX_REPOSITORY}" ramax; \
         git -C ramax checkout "${RAMAX_REF}"; \
       }

WORKDIR /src/ramax

RUN hdf5_cflags="$(pkg-config --cflags hdf5)" \
    && hdf5_libs="$(pkg-config --libs hdf5)" \
    && export CFLAGS="${hdf5_cflags}" \
    && export CXXFLAGS="${hdf5_cflags}" \
    && cmake -S . -B build \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/opt/ramax \
      -DCMAKE_INSTALL_LIBDIR=/opt/ramax/lib \
      -DCMAKE_INSTALL_PKGCONFIGDIR=/opt/ramax/lib/pkgconfig \
      -DCMAKE_INSTALL_RPATH=/opt/ramax/lib \
      -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=ON \
      -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
      -DBUILD_EXAMPLES=OFF \
      -DBUILD_SHARED_LIBS=OFF \
      -DRAMAX_NATIVE_ARCH=OFF \
      -DRAMAX_HAL_JOBS="${BUILD_JOBS}" \
      -DRAMAX_HAL_LIBS="${hdf5_libs} -lhdf5_cpp" \
    && cmake --build build --parallel "${BUILD_JOBS}" \
    && cmake --install build \
    && /opt/ramax/bin/ramax --help >/dev/null \
    && if /opt/ramax/bin/ramax --help | grep -q -- "--mask-repeats"; then exit 1; fi \
    && test ! -e /src/ramax/bin \
    && test ! -e /opt/ramax/bin/windowmasker

FROM condaforge/miniforge3:26.3.2-3

ARG TARGETARCH
ARG DEBIAN_FRONTEND=noninteractive
ARG CACTUS_VERSION=v3.2.1
ARG CACTUS_LEGACY=0
ARG CACTUS_TARBALL=
ARG MASH_VERSION=2.3

LABEL org.opencontainers.image.title="CAX"
LABEL org.opencontainers.image.description="Cactus-RaMAx workflow tooling with Cactus, RaMAx, and Mash"
LABEL org.opencontainers.image.source="https://github.com/malabz/Cax"
LABEL org.opencontainers.image.licenses="MIT"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV CONDA_PREFIX=/opt/conda
ENV PATH=/opt/conda/bin:${PATH}
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN case "${TARGETARCH:-amd64}" in \
      amd64) ;; \
      *) echo "CAX Docker builds currently support linux/amd64 only; got TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      libcurl4 \
      libgomp1 \
      libhdf5-103-1 \
      libhdf5-cpp-103-1 \
      libstdc++6 \
      libtbb12 \
      zlib1g \
    && rm -rf /var/lib/apt/lists/*

RUN conda config --system --set channel_priority flexible \
    && conda install -y \
      -c conda-forge \
      -c bioconda \
      python=3.10 \
      pip \
      tini \
      curl \
      tar \
      grep \
      sed \
      rsync \
      mash=${MASH_VERSION} \
    && conda clean -afy

COPY --from=ramax-builder /opt/ramax /opt/ramax

RUN /opt/ramax/bin/ramax --help >/dev/null \
    && ldd /opt/ramax/bin/ramax \
    && if ldd /opt/ramax/bin/ramax | grep -q "not found"; then exit 1; fi \
    && test ! -e /opt/ramax/bin/windowmasker

COPY cactus-install.sh /tmp/cactus-install.sh

RUN --mount=type=bind,source=.,target=/context,readonly \
    chmod +x /tmp/cactus-install.sh \
    && mkdir -p /tmp/cactus-download \
    && if [[ -n "${CACTUS_TARBALL}" ]]; then \
         test -f "/context/${CACTUS_TARBALL}" \
           || { echo "Missing Cactus tarball in build context: ${CACTUS_TARBALL}" >&2; exit 1; }; \
         cp "/context/${CACTUS_TARBALL}" "/tmp/cactus-download/${CACTUS_TARBALL}"; \
         cactus_tarball="/tmp/cactus-download/${CACTUS_TARBALL}"; \
       else \
         cactus_tarball=""; \
       fi \
    && CACTUS_VERSION="${CACTUS_VERSION}" \
      CACTUS_LEGACY="${CACTUS_LEGACY}" \
      CACTUS_DOWNLOAD_DIR=/tmp/cactus-download \
      CACTUS_INSTALL_ROOT=/opt \
      CACTUS_TARBALL="${cactus_tarball}" \
      GIT_CONFIG_COUNT=1 \
      GIT_CONFIG_KEY_0=safe.directory \
      GIT_CONFIG_VALUE_0='*' \
      /tmp/cactus-install.sh \
    && cactus_dir="$(find /opt -maxdepth 1 -type d -name 'cactus-bin*' | sort | head -n 1)" \
    && test -n "${cactus_dir}" \
    && ln -s "${cactus_dir}" /opt/cactus \
    && rm -rf /tmp/cactus-download /tmp/cactus-install.sh /root/.cache/pip

ENV CACTUS_DIR=/opt/cactus
ENV PATH=/opt/conda/bin:/opt/cactus/bin:/opt/ramax/bin:${PATH}
ENV PYTHONPATH=/opt/cactus/lib
ENV LD_LIBRARY_PATH=/opt/cactus/lib:/opt/ramax/lib

WORKDIR /opt/cax
COPY pyproject.toml README.md LICENSE VERSION ./
COPY cax ./cax
COPY examples ./examples

RUN python -m pip install --no-cache-dir . \
    && command -v cax \
    && command -v cactus \
    && command -v cactus-prepare \
    && command -v ramax \
    && command -v mash \
    && python -c "import cax"

RUN mkdir -p /data \
    && chmod 0777 /data

ENV HOME=/data
WORKDIR /data

ENTRYPOINT ["/opt/conda/bin/tini", "--", "/opt/conda/bin/cax"]
