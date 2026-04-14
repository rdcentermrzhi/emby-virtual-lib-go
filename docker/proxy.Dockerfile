FROM golang:1.23-alpine AS builder

WORKDIR /src/proxy
COPY proxy/go.mod proxy/go.sum ./
RUN go mod download
COPY proxy/ ./
RUN CGO_ENABLED=0 go build -o /out/emby-proxy ./cmd

FROM alpine:3.20
RUN apk add --no-cache ca-certificates tzdata
WORKDIR /app

COPY assets/placeholder.png ./assets/placeholder.png
COPY --from=builder /out/emby-proxy ./proxy/emby-proxy

EXPOSE 8000
ENTRYPOINT ["./proxy/emby-proxy"]
