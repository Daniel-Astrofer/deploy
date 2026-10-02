#!/usr/bin/env python3
"""Identity-only adapter. Every response is ordinary, unmodified stock kubectl."""
import os
import sys

CONTEXT='kind-kerosene-cell-audit-20261001'
CONFIG='/tmp/kerosene-cell-verification.EHFAhI/kubeconfig'
args=sys.argv[1:]
for flag,value in (('--context',CONTEXT),('--kubeconfig',CONFIG)):
    if flag in args:
        i=args.index(flag)
        if i+1>=len(args) or args[i+1]!=value: sys.exit('refuse other lab identity')
        del args[i:i+2]
    if any(x.startswith(flag+'=') for x in args): sys.exit('use exact separate identity arguments')
if any(x.startswith(('--server','--user','--cluster','--as')) for x in args): sys.exit('identity override forbidden')
command=['/usr/bin/kubectl','--kubeconfig',CONFIG,'--context',CONTEXT]
os.execv(command[0],command+args)
