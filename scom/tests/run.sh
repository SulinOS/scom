#!/bin/sh

rm -rf scom_root

mkdir -p scom_root/var/log
mkdir -p scom_root/var/db

cd ..
cmake .
make
make install DESTDIR=tests/scom_root
cd tests

echo

scom_root/usr/sbin/scom --datadir=scom_root/var/db/scom3 --logdir=scom_root/var/log/scom3 --debug --print
