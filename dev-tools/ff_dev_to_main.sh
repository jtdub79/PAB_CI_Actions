#!/usr/bin/env bash
git switch dev
git fetch origin
git merge --ff-only origin/main
git push origin dev
