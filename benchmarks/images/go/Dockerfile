# The image the checks of Go benchmark tasks run in (no network at check time: GOPROXY=off).
#   docker build -t engineering-team-bench-go:1 benchmarks/images/go
# The Go tasks use the standard library only.
FROM golang:1.26-bookworm
ENV GOFLAGS=-mod=mod GOPROXY=off GOTOOLCHAIN=local CGO_ENABLED=0
WORKDIR /opt/bench/app
