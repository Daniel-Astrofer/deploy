#!/usr/bin/env python3
"""Operator CLI for an explicitly keyed, offline release publication."""

import argparse
import sys

sys.dont_write_bytecode = True
from archive import canonical_bytes
from publication import PublicationError, ROLES, publish


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Private signers are explicit local PKCS#8 PEM references, never discovered. "
        "Packaging signatures establish local integrity only. No build, upload or deploy occurs."))
    for name in ("release-lock", "package-descriptor", "artifacts", "output", "trusted-root", "packaging-key", "publication-store"):
        parser.add_argument("--" + name, required=True)
    history = parser.add_mutually_exclusive_group(required=True)
    history.add_argument("--initial-publication", action="store_true", help="explicit bootstrap in an empty store; all role versions must be 1")
    history.add_argument("--previous-publication", help="prior verified immutable bundle, read only")
    for role in ROLES:
        parser.add_argument("--" + role + "-version", required=True, type=int)
        parser.add_argument("--" + role + "-expires", required=True, help="UTC YYYY-MM-DDTHH:MM:SSZ")
    parser.add_argument("--tuf-signer", action="append", required=True, metavar="ROLE:KEYID=PEM_PATH",
                        help="repeat for every explicitly authorized threshold signer")
    args = parser.parse_args(argv)
    try:
        signers = {role: {} for role in ROLES}
        for reference in args.tuf_signer:
            identity, separator, path = reference.partition("=")
            role, colon, keyid = identity.partition(":")
            if not separator or not colon or not path or role not in ROLES or keyid in signers[role]:
                raise PublicationError("invalid/duplicate explicit TUF signer reference")
            signers[role][keyid] = path
        result = publish(args.release_lock, args.package_descriptor, args.artifacts, args.output,
                         publication_store=args.publication_store,
                         trusted_root=args.trusted_root, signers=signers, packaging_key=args.packaging_key,
                         versions={r: getattr(args, r + "_version") for r in ROLES},
                         expires={r: getattr(args, r + "_expires") for r in ROLES},
                         previous=args.previous_publication, initial=args.initial_publication)
        print(canonical_bytes(result).decode("utf-8"))
        return 0
    except ValueError as error:
        print("publication failed: " + str(error), file=sys.stderr)
        return 1
    except (OSError, RuntimeError, RecursionError) as error:
        # Never echo private key bytes, subprocess output, or signer references.
        print("publication failed (" + type(error).__name__ + "); no authorization or deployment performed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
