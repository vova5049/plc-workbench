# Reproducing checks from a review archive

A source-only review archive can reproduce offline code and generator tests,
but does not include Git history. A supplied checkout or Git bundle can also
reproduce the history it contains. Neither format establishes which other
commits, branches, PR refs or cached objects exist on GitHub.

## Source archive, without Git metadata

Extract the archive into a directory outside any other Git checkout. From its
`source` directory, use Python 3.10 or later with pytest and the Git executable
installed. Synthetic scanner tests invoke Git even though this source snapshot
has no Git metadata:

```sh
PYTHONPATH=src python3 -m pytest -q
PYTHONPATH=src python3 examples/lighting-case-study/generate.py --out /tmp/lighting-review
sha256sum --check MANIFEST.sha256
```

The suite explicitly skips the test that audits this actual checkout when Git
metadata is absent. Synthetic Git fixtures still exercise the scanner itself;
these fixtures do not attest to the history of the delivered source snapshot.
The full-suite log identifies the environment and skipped test count.

`check_public.py` requires the actual Git checkout to enumerate versioned files
and commit metadata. Without a valid checkout it returns exit 3, indicating an
incomplete audit. `check_history.py` also requires actual local reachable history.
Creating an empty repository with `git init` does not recover the missing history
and must not be used to claim a publication audit passed.

## Git bundle

Select the branch explicitly when restoring a supplied bundle:

```sh
git clone --branch main /path/to/review.bundle plc-workbench-review
cd plc-workbench-review
git remote remove origin
```

Replace the bundle path with the supplied file. A bundle containing only
`refs/heads/main` may not advertise a default `HEAD`; plain `git clone` can then
leave the worktree without a checkout. Confirm the expected commit and manifest
before running the actual-checkout checks below. Removing `origin` here removes
the local bundle path, not a GitHub repository.

## Actual Git checkout

After obtaining the exact reviewed revision and its history, run from that
checkout's root:

```sh
PYTHONPATH=src python3 -m pytest -q
python3 scripts/check_public.py
python3 scripts/check_history.py
```

The two scanners require the owner's private patterns. A reviewer without them
can use `--allow-missing-private` to run generic rules only, with that limitation
recorded explicitly. Neither scanner proves that remote PR or cached content
has been removed. A new publication commit/root needs its own CI and a separate
review of remote retention before changing visibility.

The lighting generator stages and validates every station before publishing
the result files. A validation refusal preserves existing destination files.
This is not a transaction across multiple files if a filesystem write fails
during the final publication step.
