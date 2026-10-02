// Synthetic fixture creation and real read-only consistency checking.
package main

import (
	"fmt"
	bolt "go.etcd.io/bbolt"
	"os"
	"time"
)

func run() error {
	if len(os.Args) != 3 {
		return fmt.Errorf("usage: bbolt check|fixture FILE")
	}
	mode, path := os.Args[1], os.Args[2]
	if mode != "check" && mode != "fixture" {
		return fmt.Errorf("invalid mode")
	}
	db, err := bolt.Open(path, 0600, &bolt.Options{ReadOnly: mode == "check", Timeout: time.Second})
	if err != nil {
		return err
	}
	defer db.Close()
	if mode == "fixture" {
		return db.Update(func(tx *bolt.Tx) error {
			b, err := tx.CreateBucket([]byte("SYNTHETIC-NOT-LND-RECOVERY"))
			if err != nil {
				return err
			}
			for i := 0; i < 256; i++ {
				if err := b.Put([]byte(fmt.Sprintf("key-%04d", i)), []byte("disposable-lab-only")); err != nil {
					return err
				}
			}
			return nil
		})
	}
	return db.View(func(tx *bolt.Tx) error {
		count := 0
		for err := range tx.Check() {
			count++
			_ = err
		}
		if count != 0 {
			return fmt.Errorf("bbolt consistency errors: %d", count)
		}
		fmt.Println("bbolt read-only check passed")
		return nil
	})
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(78)
	}
}
