package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/cometbft/cometbft/abci/server"
	"github.com/cometbft/cometbft/libs/log"
)

func main() {
	if len(os.Args) < 2 {
		fail(fmt.Errorf("use serve or verify"))
	}
	flags := flag.NewFlagSet(os.Args[1], flag.ExitOnError)
	policyPath := flags.String("policy", "", "operator-provisioned static governance proposer policy")
	statePath := flags.String("state", "", "protected application state file")
	address := flags.String("listen", "", "local ABCI UNIX socket in an operator-owned private directory")
	anchorPath := flags.String("trusted-anchor", "", "out-of-band immutable light block and policy")
	proofPath := flags.String("proof", "", "untrusted offline contiguous consensus proof")
	releaseDigest := flags.String("release-digest", "", "canonical release lock SHA256")
	sequence := flags.Uint64("sequence", 0, "expected release sequence")
	if err := flags.Parse(os.Args[2:]); err != nil {
		fail(err)
	}
	switch os.Args[1] {
	case "verify":
		raw, err := boundedRead(*anchorPath)
		if err != nil {
			fail(err)
		}
		var anchor TrustAnchor
		if err := strictDecode(raw, &anchor); err != nil {
			fail(err)
		}
		raw, err = boundedRead(*proofPath)
		if err != nil {
			fail(err)
		}
		var proof ConsensusProof
		if err := strictDecode(raw, &proof); err != nil {
			fail(err)
		}
		result, err := verifyConsensus(anchor, proof, *releaseDigest, *sequence, time.Now())
		if err != nil {
			fail(err)
		}
		if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
			fail(err)
		}
	case "serve":
		if len(*address) < 7 || (*address)[:7] != "unix://" {
			fail(fmt.Errorf("ABCI listener must be operator-protected UNIX socket"))
		}
		socketPath := (*address)[7:]
		parent, err := os.Lstat(filepath.Dir(socketPath))
		if !filepath.IsAbs(socketPath) || err != nil || !parent.IsDir() || parent.Mode().Perm()&0077 != 0 {
			fail(fmt.Errorf("ABCI socket parent must be a private real directory"))
		}
		raw, err := boundedRead(*policyPath)
		if err != nil {
			fail(err)
		}
		var policy Policy
		if err := strictDecode(raw, &policy); err != nil {
			fail(err)
		}
		application, err := newApplication(*statePath, policy)
		if err != nil {
			fail(err)
		}
		srv, err := server.NewServer(*address, "socket", application)
		if err != nil {
			fail(err)
		}
		srv.SetLogger(log.NewTMLogger(log.NewSyncWriter(os.Stderr)))
		if err := srv.Start(); err != nil {
			fail(err)
		}
		if err := os.Chmod(socketPath, 0600); err != nil {
			_ = srv.Stop()
			fail(err)
		}
		stop := make(chan os.Signal, 1)
		signal.Notify(stop, syscall.SIGTERM, syscall.SIGINT)
		<-stop
		if err := srv.Stop(); err != nil {
			fail(err)
		}
	default:
		fail(fmt.Errorf("unknown command"))
	}
}
