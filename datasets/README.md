# Datasets

Only the Bank Marketing demo dataset is versioned in this repository. Other
benchmark and local datasets may remain in this directory during development,
but `.gitignore` excludes them from commits.

## Bank Marketing

Committed files:

- `bank+marketing/bank/bank.csv`: 4,521-row sample, semicolon-delimited
- `bank+marketing/bank/bank-names.txt`: schema, provenance, and citation notes
- `bank+marketing/request.txt`: example analysis request

The classification target is `y`, which indicates whether a client subscribed
to a term deposit.

Source: [UCI Machine Learning Repository, Bank Marketing](https://archive.ics.uci.edu/dataset/222/bank+marketing)

Citation:

> Moro, S., Rita, P., & Cortez, P. (2014). Bank Marketing [Dataset]. UCI
> Machine Learning Repository. https://doi.org/10.24432/C5K306

The UCI dataset page distributes this dataset under the
[Creative Commons Attribution 4.0 International license](https://creativecommons.org/licenses/by/4.0/).
This dataset license is separate from the licensing terms of the project source
code.

Run the included sample:

```powershell
python main.py --data "datasets/bank+marketing/bank/bank.csv" --description-file "datasets/bank+marketing/bank/bank-names.txt" --request "Analyze term-deposit subscription, train candidate classifiers, and produce an evidence-backed business report" --target y --task-type classification
```
