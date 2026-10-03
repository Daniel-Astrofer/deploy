package main

import (
	"context"
	"errors"
	"net"
	"net/http"
	"testing"
)

func TestAdmissionServiceCancelledBeforeBind(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := serveAdmissionContext(ctx, &http.Server{Addr: "127.0.0.1:0"}); !errors.Is(err, context.Canceled) {
		t.Fatalf("expected cancellation before bind, got %v", err)
	}
}

func TestAdmissionServiceBindFailure(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	if err := serveAdmissionContext(context.Background(), &http.Server{Addr: listener.Addr().String()}); err == nil {
		t.Fatal("accepted occupied listener")
	}
}

func TestAdmissionServiceRejectsInvalidProfileBeforeListener(t *testing.T) {
	if err := runAdmissionService(context.Background(), "/nonexistent/admission.json", "", nil); err == nil {
		t.Fatal("accepted absent configuration authority")
	}
}
