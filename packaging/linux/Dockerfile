# syntax=docker/dockerfile:1
#
# Build the musl-linked librsvg / libemf2svg helpers for one Linux architecture.
# The host orchestrator (packaging/build_linux_helpers.sh) runs this with
# --platform linux/arm64 and linux/amd64 and exports the final scratch stage.

FROM alpine:3.22 AS build

RUN apk add --no-cache \
        argp-standalone \
        build-base \
        cmake \
        fontconfig-dev \
        freetype-dev \
        git \
        libpng-dev \
        libxml2-dev \
        pkgconf \
        rsvg-convert

RUN git clone --depth 1 --branch 1.8.1 \
        https://github.com/kakwa/libemf2svg.git /src

RUN cmake -S /src -B /src/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DUNITTEST=OFF \
    && cmake --build /src/build -j"$(nproc)" \
    && cmake --install /src/build

COPY fonts.conf /usr/local/share/cp4ai/fonts.conf
COPY build_helpers.sh /usr/local/bin/build_helpers.sh
RUN chmod +x /usr/local/bin/build_helpers.sh \
    && /usr/local/bin/build_helpers.sh /out

FROM scratch AS bundle
COPY --from=build /out /
