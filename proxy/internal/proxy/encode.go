package proxy

import (
	"bytes"
	"compress/flate"
	"compress/gzip"
	"io"

	"github.com/andybalholm/brotli"
)

func encodeBodyByContentEncoding(body []byte, encoding string) ([]byte, error) {
	var buf bytes.Buffer
	switch encoding {
	case "gzip":
		gz := gzip.NewWriter(&buf)
		if _, err := gz.Write(body); err != nil {
			return nil, err
		}
		if err := gz.Close(); err != nil {
			return nil, err
		}
		return buf.Bytes(), nil
	case "deflate":
		df, err := flate.NewWriter(&buf, flate.DefaultCompression)
		if err != nil {
			return nil, err
		}
		if _, err := df.Write(body); err != nil {
			return nil, err
		}
		if err := df.Close(); err != nil {
			return nil, err
		}
		return buf.Bytes(), nil
	case "br":
		br := brotli.NewWriter(&buf)
		if _, err := br.Write(body); err != nil {
			return nil, err
		}
		if err := br.Close(); err != nil {
			return nil, err
		}
		return buf.Bytes(), nil
	default:
		return body, nil
	}
}

func decodeResponseBody(encoding string, body io.ReadCloser) ([]byte, error) {
	defer body.Close()
	switch encoding {
	case "br":
		return io.ReadAll(brotli.NewReader(body))
	case "deflate":
		df := flate.NewReader(body)
		defer df.Close()
		return io.ReadAll(df)
	case "gzip":
		gz, err := gzip.NewReader(body)
		if err != nil {
			return nil, err
		}
		defer gz.Close()
		return io.ReadAll(gz)
	default:
		return io.ReadAll(body)
	}
}
