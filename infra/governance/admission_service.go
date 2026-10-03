package main

import (
	"context"
	"errors"
	"log"
	"net"
	"net/http"
	"time"
)

// No listener is opened until protected authority and the registry connection
// have passed validation. Shutdown never deletes consumed admission records.
func runAdmissionService(ctx context.Context, path, digest string, logger *log.Logger) error {
	config, err := loadAdmissionServiceConfig(path, digest)
	if err != nil {
		return err
	}
	anchor, operators, transport, err := admissionServiceAuthority(*config)
	if err != nil {
		return err
	}
	db, err := openAdmissionRegistry(config.Registry)
	if err != nil {
		return err
	}
	defer db.Close()
	probe, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	var database, user string
	if err := db.QueryRowContext(probe, "SELECT current_database(), current_user").Scan(&database, &user); err != nil || database != config.Registry.Database || user != config.Registry.User {
		return errors.New("admission registry identity unavailable or mismatched")
	}
	server, err := newAdmissionServer(config.Listen, &admissionHTTPHandler{DB: db, Anchor: anchor, Operators: operators.Operators, Cells: config.Cells}, transport, logger)
	if err != nil {
		return err
	}
	return serveAdmissionContext(ctx, server)
}

func serveAdmissionContext(ctx context.Context, server *http.Server) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	listener, err := net.Listen("tcp", server.Addr)
	if err != nil {
		return errors.New("admission listener unavailable")
	}
	defer listener.Close()
	done := make(chan error, 1)
	go func() { done <- server.ServeTLS(listener, "", "") }()
	select {
	case err := <-done:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return errors.New("admission listener failed")
	case <-ctx.Done():
		shutdown, cancel := context.WithTimeout(context.Background(), 45*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdown); err != nil {
			_ = server.Close()
			<-done
			return errors.New("admission shutdown exceeded deadline; inspect uncertain admissions")
		}
		if err := <-done; err != nil && !errors.Is(err, http.ErrServerClosed) {
			return errors.New("admission listener failed during shutdown")
		}
		return nil
	}
}
