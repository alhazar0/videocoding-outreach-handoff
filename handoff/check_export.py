#!/usr/bin/env python3
"""Scan Git-tracked files; optionally compare email literals with a private DB.

No credentials are read. Private addresses are only reported as hash prefixes.
"""
import argparse
import hashlib
import json
import pathlib
import re
import sqlite3
import subprocess


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--git',default='git')
    p.add_argument('--private-db',type=pathlib.Path)
    p.add_argument('--out',type=pathlib.Path)
    a=p.parse_args();root=pathlib.Path(__file__).resolve().parents[1]
    paths=[x for x in subprocess.run([a.git,'ls-files','-z'],cwd=root,capture_output=True,check=True).stdout.decode().split('\0') if x]
    addresses=set()
    if a.private_db:
        db=sqlite3.connect(a.private_db.resolve().as_uri()+'?mode=ro',uri=True)
        addresses={x[0].lower() for x in db.execute('select email from contacts union select registered_email from creator_profiles where registered_email is not null') if x[0]}
        db.close()
    hits=[];tokens=[];forbidden=[]
    patterns=[r'sk_live_[A-Za-z0-9]{12,}',r'sk-proj-[A-Za-z0-9_-]{20,}',r'gh[pousr]_[A-Za-z0-9]{20,}',r'AIza[A-Za-z0-9_-]{25,}',r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',r'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}']
    for rel in paths:
        f=root/rel
        if f.is_symlink() or any(x in pathlib.Path(rel).parts for x in ['private','node_modules','.wrangler']) or f.suffix in ['.sqlite','.db','.pem','.key']:
            forbidden.append(rel)
        try: text=f.read_text()
        except UnicodeDecodeError: continue
        for address in re.findall(r'[A-Za-z0-9_.%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}',text):
            if address.lower() in addresses and not address.endswith('@videocoding.ai'):
                hits.append({'file':rel,'sha256_prefix':hashlib.sha256(address.encode()).hexdigest()[:10]})
        if any(re.search(x,text) for x in patterns): tokens.append(rel)
    result={'tracked_files_scanned':len(paths),'exact_contact_or_registered_email_hits':hits,'credential_pattern_hits':tokens,'forbidden_file_hits':forbidden,'private_db_comparison_performed':bool(a.private_db),'scope':'Current tracked file contents; new export has no imported history. Pattern scan cannot prove absence of every possible secret.'}
    if a.out:a.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
    raise SystemExit(1 if hits or tokens or forbidden else 0)


if __name__=='__main__':main()
