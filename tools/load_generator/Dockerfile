FROM golang:1.25-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
COPY *.go ./
RUN CGO_ENABLED=0 go build -o /loadgen .

FROM alpine:3.20
COPY --from=build /loadgen /usr/local/bin/loadgen
ENTRYPOINT ["loadgen"]
