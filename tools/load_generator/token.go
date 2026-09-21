package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

// mintToken produces the same HS256 JWT the API issues at login.
//
// The generator signs its own tokens rather than calling /auth/login for every
// client. bcrypt is deliberately slow - roughly 250ms per verification - so
// twenty thousand logins would take over an hour and would measure bcrypt
// rather than the price update path. Login is still exercised once at startup,
// as a preflight, so a broken auth path fails the run loudly.
func mintToken(secret string, userID int, username string, ttl time.Duration) (string, error) {
	header := map[string]string{"alg": "HS256", "typ": "JWT"}
	now := time.Now()
	claims := map[string]any{
		"sub":      fmt.Sprintf("%d", userID),
		"username": username,
		"iat":      now.Unix(),
		"exp":      now.Add(ttl).Unix(),
	}

	encode := func(v any) (string, error) {
		raw, err := json.Marshal(v)
		if err != nil {
			return "", err
		}
		return base64.RawURLEncoding.EncodeToString(raw), nil
	}

	h, err := encode(header)
	if err != nil {
		return "", err
	}
	c, err := encode(claims)
	if err != nil {
		return "", err
	}

	signing := h + "." + c
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(signing))
	sig := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))

	return strings.Join([]string{h, c, sig}, "."), nil
}
