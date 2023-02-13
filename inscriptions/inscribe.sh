#!/bin/sh
ord_bin=./ord
btc_datadir=/Bitcoin/
datadir=/ord/
while IFS= read -r line; do
  echo "inscribing 600-$line.svg..."
  echo "$ord_bin --bitcoin-data-dir $btc_datadir --data-dir $datadir wallet inscribe imgs/600-$line.svg --fee-rate 4"
done < ord-colors.txt
