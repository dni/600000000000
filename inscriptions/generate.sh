#!/bin/sh
inc=0xffffff
while [[ $inc -ge 0 ]]; do
    hex=$(printf %06x $(( $inc - 60000)))
    echo $hex
    sed -e "s/__marker__/$hex/" 600-template.svg > imgs2/600-$hex.svg
    inc=$(( $inc - 696969 ))
done;
