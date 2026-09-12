# Third-party source notices

The repository MIT license applies to repository-authored code. It does not
relicense the third-party data or their derivatives in this evidence package.
Retain this notice when redistributing the workload artifacts.

## WorldCup98

Attribution: M. Arlitt and T. Jin, "1998 World Cup Web Site Access Logs", August
1998. Available at http://www.acm.org/sigcomm/ITA/.

Source and permissions: [Internet Traffic Archive](https://ita.ee.lbl.gov/html/contrib/WorldCup.html).
The four `wc98_*_1s.csv` files contain selected aggregate request-count windows,
not original request records. Transformations include decoding, per-server and
local-day aggregation, window selection, zero-filled absent seconds, and
normalization into plateaus recorded in provenance. Candidates and extraction
summaries are derived aggregate metadata. The HP decode tools are not bundled.

Log redistribution notice, reproduced unmodified from the source:

> You have permission to use and redistribute these access logs freely, as long as this Copyright and Disclaimer is distributed unmodified. If you publish any results based on these access logs, please send us a copy of this publication (in electronic or print form) and give the following reference or attribution in your publication:
>
> M. Arlitt and T. Jin, "1998 World Cup Web Site Access Logs", August 1998. Available at http://www.acm.org/sigcomm/ITA/.

The source's Copyright and Disclaimer is retained below. No HP software is
included and this notice does not apply HP's software terms to repository code.

```text
               Copyright (C) 1997, 1998, 1999 Hewlett-Packard Company
                             ALL RIGHTS RESERVED.

      The enclosed software and documentation includes copyrighted works
      of Hewlett-Packard Co. For as long as you comply with the following
      limitations, you are hereby authorized to (i) use, reproduce, and
      modify the software and documentation, and to (ii) distribute the
      software and documentation, including modifications, for
      non-commercial purposes only.

      1.  The enclosed software and documentation is made available at no
          charge in order to advance the general development of
          the Internet, the World-Wide Web, and Electronic Commerce.

      2.  You may not delete any copyright notices contained in the
          software or documentation. All hard copies, and copies in
          source code or object code form, of the software or
          documentation (including modifications) must contain at least
          one of the copyright notices.

      3.  The enclosed software and documentation has not been subjected
          to testing and quality control and is not a Hewlett-Packard Co.
          product. At a future time, Hewlett-Packard Co. may or may not
          offer a version of the software and documentation as a product.

      4.  THE SOFTWARE AND DOCUMENTATION IS PROVIDED "AS IS".
          HEWLETT-PACKARD COMPANY DOES NOT WARRANT THAT THE USE,
          REPRODUCTION, MODIFICATION OR DISTRIBUTION OF THE SOFTWARE OR
          DOCUMENTATION WILL NOT INFRINGE A THIRD PARTY'S INTELLECTUAL
          PROPERTY RIGHTS. HP DOES NOT WARRANT THAT THE SOFTWARE OR
          DOCUMENTATION IS ERROR FREE. HP DISCLAIMS ALL WARRANTIES,
          EXPRESS AND IMPLIED, WITH REGARD TO THE SOFTWARE AND THE
          DOCUMENTATION. HP SPECIFICALLY DISCLAIMS ALL WARRANTIES OF
          MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE.

      5.  HEWLETT-PACKARD COMPANY WILL NOT IN ANY EVENT BE LIABLE FOR ANY
          DIRECT, INDIRECT, SPECIAL, INCIDENTAL OR CONSEQUENTIAL DAMAGES
          (INCLUDING LOST PROFITS) RELATED TO ANY USE, REPRODUCTION,
          MODIFICATION, OR DISTRIBUTION OF THE SOFTWARE OR DOCUMENTATION.
```

## RetailRocket

Source: RetailRocket, *Retailrocket recommender system dataset*, published by
Roman Zykov on [Kaggle](https://www.kaggle.com/datasets/retailrocket/ecommerce-dataset).
The source lists [Creative Commons Attribution-NonCommercial-ShareAlike 4.0
International](https://creativecommons.org/licenses/by-nc-sa/4.0/).

`rr_periodic_1s.csv` and its derived provenance, candidate and extraction
metadata are distributed under CC BY-NC-SA 4.0. Changes: removal of sessions
identified by the documented bot threshold, aggregation into one-second event
counts, selection of one daily window, and plateau normalization. No individual
event or person records are included. Preserve attribution, indicate changes,
use the same license for adaptations, and observe the noncommercial restriction.
No endorsement by the data providers is implied.
