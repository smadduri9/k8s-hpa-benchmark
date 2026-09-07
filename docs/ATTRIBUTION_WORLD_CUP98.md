# World Cup 1998 access logs — attribution

Source: [1998 World Cup Web Site Access Logs](https://ita.ee.lbl.gov/html/contrib/WorldCup.html) (ITA / ACM SIGCOMM).

## Log redistribution (verbatim from ITA)

You have permission to use and redistribute these access logs freely, as long as this Copyright and Disclaimer is distributed unmodified. If you publish any results based on these access logs, please send us a copy of this publication (in electronic or print form) and give the following reference or attribution in your publication:

> M. Arlitt and T. Jin, "1998 World Cup Web Site Access Logs", August 1998. Available at http://www.acm.org/sigcomm/ITA/.

## HP decode tools (not used in this repository)

The ITA tool archive (`read`, `checklog`, `recreate`) carries a separate Hewlett-Packard copyright permitting distribution **for non-commercial purposes only**. This repository does **not** vendor, commit, or execute those tools. Binary logs are read with a repo-owned decoder of the published packed 20-byte `struct request` layout.

## Collection bounds

Requests were collected between **April 30, 1998** and **July 26, 1998** (ITA page). Timestamps in the binary log are GMT epoch seconds; local time at the tournament was **France +0200**.
