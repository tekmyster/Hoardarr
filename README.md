# Hoardarr

Storage lifecycle management for the ARR ecosystem.

Hoardarr is a storage lifecycle management platform for homelab
operators and large media hoarders running the ARR stack. It provides a
unified control plane and web interface on top of proven Linux storage
tools such as mergerfs, ZFS, and SnapRAID.

## Website

https://hoardarr.com

The Hoardarr website hosts conceptual UI mockups that illustrate the
intended ARR-style interface and storage lifecycle workflows.

## Overview

Hoardarr allows operators to manage heterogeneous storage backends
including:

-   individual disks
-   SnapRAID parity-protected disk sets
-   ZFS pools
-   mixed storage backends
-   foreign storage systems


## Why Hoardarr Exists

Most NAS platforms assume:

-   clean initial architecture
-   static pools
-   planned hardware refresh cycles

Real homelab storage environments evolve gradually:

-   disks are acquired opportunistically
-   storage pools accumulate over time
-   RAID decisions age poorly
-   migrating arrays is difficult and disruptive
-   ARR tools require stable paths

Hoardarr provides lifecycle-aware storage management for these
environments.

## What Hoardarr Is

Hoardarr is an ARR-first storage control plane that allows operators to:

-   grow storage incrementally
-   mix ZFS and SnapRAID strategies
-   drain and retire aging disks safely
-   ingest foreign storage systems
-   maintain stable media paths
-   monitor disk health and parity freshness
-   integrate with homelab automation systems

Hoardarr treats storage as a lifecycle rather than a static pool.

## Core Architecture

Hoardarr orchestrates existing Linux storage tools:

-   **ZFS** for protected pools and scrubs
-   **SnapRAID** for parity protection
-   **mergerfs** for unified namespaces
-   **SMART telemetry** for disk health
-   lifecycle workflows for migration and retirement

## Primary Use Cases

Hoardarr is designed for operators who need to:

-   grow media storage gradually
-   maintain stable paths for ARR tools
-   migrate data from older disks
-   mix storage protection strategies
-   import data from legacy NAS systems
-   ingest external archive drives

## Safety Model

Hoardarr enforces guardrails before destructive operations such as:

-   disk wipes
-   backend removal
-   storage retirement
-   drain operations

Checks include:

-   capacity validation
-   health checks
-   parity freshness checks
-   topology validation

Destructive operations require explicit confirmation and are logged.

## Observability

Hoardarr exposes telemetry including:

-   disk SMART metrics
-   ZFS health
-   SnapRAID sync status
-   storage capacity
-   namespace usage
-   job progress

Metrics are exposed via a Prometheus-compatible endpoint.

## Project Status

Hoardarr is currently in the design and architecture phase.

## Vision

Hoardarr enables homelab operators to treat storage as an evolving
system rather than a fixed appliance.
