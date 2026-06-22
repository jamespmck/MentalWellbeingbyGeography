# PHN and LGA bridge context layer v05

This layer adds validated PHN and LGA context fields to the SA2 master where source bridges can be built.

PHN 2017 is the preferred PHN boundary context for the 2021/2022 evidence window. PHN 2023 may optionally be added as current-boundary context.

The LGA field is a dominant-LGA assignment derived from the ABS 2021 LGA allocation file and 2021 Mesh Block to SA2 allocation. Where an SA2 spans multiple LGAs, the dominant LGA is selected by Mesh Block area share. The full area bridge is retained for future source-specific allocation.

These fields are primarily bridge/context fields. They should not be treated as direct predictors without considering geography, boundary and allocation assumptions.
