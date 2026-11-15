#!/usr/bin/env bash
# Shared Kubuntu/A-B image capacity and compatibility contract.
# shellcheck disable=SC2034
# This file is sourced by the ISO builder, installer, OTA builder, and updater.
ZARYA_AB_SLOT_GIB=20
ZARYA_MAX_ROOTFS_GIB=18
ZARYA_MAX_OTA_PAYLOAD_GIB=12
ZARYA_MIN_DISK_GIB=64
ZARYA_OTA_FORMAT=2
ZARYA_OTA_BASE=ubuntu-resolute
