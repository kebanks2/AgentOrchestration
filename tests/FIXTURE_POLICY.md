# Test Fixture Data Policy

Test fixtures must use synthetic values by default. Do not commit realistic
customer payloads, private keys, bearer tokens, cloud access keys, credit card
numbers, Social Security numbers, or personal email samples.

If a regression requires a realistic-looking shape, sanitize it first and add an
explicit approval note in the fixture review. The fixture sanitization test runs
with the normal pytest suite so data-related fixture paths are checked in CI.
