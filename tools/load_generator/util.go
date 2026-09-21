package main

import (
	"io"
	"strings"
)

func stringReader(s string) io.Reader { return strings.NewReader(s) }
