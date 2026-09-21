FROM golang:1.24-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY *.go ./
RUN CGO_ENABLED=0 go build -o /loadgen .

FROM alpine:3.20
COPY --from=build /loadgen /usr/local/bin/loadgen
ENTRYPOINT ["loadgen"]
