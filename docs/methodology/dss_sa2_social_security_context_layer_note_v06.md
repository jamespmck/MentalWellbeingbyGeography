# DSS SA2 social-security context layer v06-wide-fix-v3

Selected DSS reporting period: `2021-12-01`

The script uses the DSS historical SA2 2016 machine-readable file because it contains the 2021/2022 window. The DSS 2021-SA2 machine-readable file begins later, from June 2023, so it is not used for the primary aligned context layer.

The layer retains selected payment-recipient count concepts relevant to socioeconomic, disability, caring, family and housing context, then allocates SA2 2016 counts to SA2 2021 using the official ABS SA2 2016 to SA2 2021 correspondence already built in this project.

Allocation retained across selected concepts: `99.99998399794295`

Limitations:

- DSS fields are counts, not rates.
- Counts partly reflect population size.
- Use denominators or population controls before substantive modelling interpretation.
- Allocation from SA2 2016 to SA2 2021 adds correspondence uncertainty.
- DSS confidentiality treatment affects small cells. Review the DSS source notes and the project audits.
