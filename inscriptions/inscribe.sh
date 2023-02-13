#!/bin/sh
ord_bin=./ord
btc_datadir=/Bitcoin/
datadir=/ord/
repeat() { while :; do $@ && return; sleep 30; done }
while IFS= read -r line; do
  echo "inscribing 600-$line.svg..."
  repeat echo "$ord_bin --bitcoin-data-dir $btc_datadir --data-dir $datadir wallet inscribe imgs2/600-$line.svg --fee-rate 4"
done < ord-colors2.txt
