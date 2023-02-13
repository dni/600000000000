#!/bin/sh
inc=0xffffff
while [[ $inc -ge 0 ]]; do
    hex=$(printf %06x $inc)
    echo $hex
    sed -e "s/__marker__/$hex/" 600-template.svg > imgs/600-$hex.svg
    inc=$(( $inc - 66666 ))
done;
