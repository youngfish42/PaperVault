# PDF abstract extraction validation

- date: 2026-10-21 18:03
- sample-size: 24, seed: 42
- referenced: 19, PASS: 16 (84%), REVIEW: 1, FAIL: 2, NOREF: 5

| # | verdict | ratio | stratum | conf | len | copyright | intro-leak | kw-leak | title |
|---|---------|-------|---------|------|-----|-----------|------------|---------|-------|
| 1 | NOREF | - | ceur-ws | WWW2007 | 1410 |  |  |  | OntoWiki: A Tool for Social, Semantic Collaboration |
| 2 | PASS | 0.96 | ceur-ws | ASE2010 | 1640 |  |  |  | Reasoning about Consistency in Model Merging |
| 3 | FAIL | 0.00 | ceur-ws | WWW2014 | 0 |  |  |  | Sentiment Analysis of Wimbledon Tweets |
| 4 | PASS | 0.94 | ceur-ws | RECSYS2016 | 1012 |  |  |  | Representing Items as Word-Embedding Vectors and Generating  |
| 5 | NOREF | - | ceur-ws | SIGIR2017 | 1283 |  |  |  | Enhancing Categorization of Computer Science Research Papers |
| 6 | NOREF | - | ceur-ws | IJCAI2018 | 420 |  |  |  | MDL for FCA: Is There a Place for Background Knowledge? |
| 7 | PASS | 1.00 | vldb | VLDB2009 | 1052 |  |  |  | Randomized Multi-pass Streaming Skyline Algorithms |
| 8 | NOREF | - | vldb | VLDB2012 | 1134 |  |  |  | Scuba: Diving into Data at Facebook |
| 9 | PASS | 0.96 | vldb | VLDB2015 | 1265 |  |  |  | Parallel Local Graph Clustering |
| 10 | REVIEW | 0.71 | vldb | VLDB2020 | 1539 |  |  |  | Local Dampening: Differential Privacy for Non-numeric Querie |
| 11 | PASS | 1.00 | vldb | VLDB2022 | 1869 |  |  |  | Odyssey: An Engine Enabling The Time-Series Clustering Journ |
| 12 | FAIL | 0.00 | vldb | VLDB2023 | 0 |  |  |  | Aleph Filter: To Infinity in Constant Time |
| 13 | PASS | 1.00 | ndss | NDSS2018 | 1026 |  |  |  | Revisiting Private Stream Aggregation: Lattice-Based PSA |
| 14 | PASS | 0.95 | ndss | NDSS2018 | 1280 |  |  |  | K-means++ vs. Behavioral Biometrics: One Loop to Rule Them A |
| 15 | PASS | 0.86 | ndss | NDSS2018 | 1662 |  |  |  | Enhancing Memory Error Detection for Large-Scale Application |
| 16 | PASS | 1.00 | ndss | NDSS2018 | 1852 |  |  |  | Towards a Timely Causality Analysis for Enterprise Security |
| 17 | PASS | 1.00 | usenix | USS2008 | 643 |  |  |  | Coercion-Resistant Tallying for STV Voting |
| 18 | PASS | 1.00 | usenix | USS2008 | 1506 |  |  |  | An Improved Clock-skew Measurement Technique for Revealing H |
| 19 | PASS | 0.96 | usenix | USS2009 | 1499 |  |  |  | Vanish: Increasing Data Privacy with Self-Destructing Data |
| 20 | PASS | 0.99 | usenix | USS2010 | 847 |  |  |  | AdJail: Practical Enforcement of Confidentiality and Integri |
| 21 | PASS | 0.83 | other | IJCAI2003 | 603 |  |  |  | Coupling CSP Decomposition Methods and Diagnosis Algorithms  |
| 22 | PASS | 0.70 | other | VLDB2014 | 1872 |  |  |  | Multi-Version Range Concurrency Control in Deuteronomy |
| 23 | NOREF | - | other | VLDB2016 | 852 |  |  |  | Strider: An Adaptive, Inference-enabled Distributed RDF Stre |
| 24 | PASS | 0.99 | other | VLDB2024 | 1039 |  |  |  | Finding Time-Proximity Communities in Temporal Heterogeneous |

## REVIEW / FAIL / NOREF details

### [1] NOREF — [WWW2007] OntoWiki: A Tool for Social, Semantic Collaboration

- url: https://ceur-ws.org/Vol-273/paper_91.pdf
- ref: none | ratio: -

**extracted**:

```
We present OntoWiki, a tool providing support for agile, distributed knowledge engineering scenarios. OntoWiki fa- cilitates the visual presentation of a knowledge base as an information map, with diﬀerent views on instance data. It enables intuitive authoring of semantic content, with an in- line editing mode for editing RDF content, similar to WYSI- WYG for text documents. It fosters social collaboration as- pects by keeping track of changes, allowing comments and discussion on every single part of a knowledge base, enabling to rate and measure the popularity of content and honor- ing the activity of users. OntoWiki enhances the browsing and retrieval by oﬀering semantic enhanced search strate- gies. All these techniques are applied with the ultimate goal of decreasing the entrance barrier for projects and domain experts to collaborate using semantic technologies. In the spirit of the Web 2.0 OntoWiki implements an ”architec- ture of participation” that allows users to add value to the application as they use it. It is available as open-source software and a demonstration platform can be accessed at http://3ba.se. Categories and Subject Descriptors H.4.m [Information Systems Applications]: Miscella- neous; K.4.3 [Computers and Society]: Organizational Impacts— Computer-supported collaborative work ; H.2.8.f [Database Management]: Database Applications— Knowledge management applications
```

### [3] FAIL — [WWW2014] Sentiment Analysis of Wimbledon Tweets

- url: https://ceur-ws.org/Vol-1141/paper_10.pdf
- ref: openalex | ratio: 0.00

**extracted**:

```
(empty)
```

**reference**:

```
Annotating videos in the absence of textual metadata is a major challenge as it involves complex image and video analytics, which is often error prone. However, if the video is a live coverage of an event, time correlated textual feed about the same event can act as a valuable source of aid for such annotation. Popular real time microblog streams like Twitter feeds can be an ideal source of such textual information. In this paper we explore the possibility of such correlation with the sentiment analysis of a set of tweets of the Roger Federer and Novak “Nole” Djokovic semi finals match at Wimbledon 2012.
```

### [5] NOREF — [SIGIR2017] Enhancing Categorization of Computer Science Research Papers using Knowledge Bases

- url: https://ceur-ws.org/Vol-1883/paper_4.pdf
- ref: none | ratio: -

**extracted**:

```
Automatic categorization of computer science research papers using just the abstracts, is a hard problem to solve. This is due to the short text length of the abstracts. Also, abstracts are a general discussion of the topic with few domain speciﬁc terms. These reasons make it hard to generate good representations of abstracts which in turn leads to poor categorization performance. To address this challenge, external Knowl- edge Bases (KB) like Wikipedia, Freebase etc. can be used to enrich the representations for abstracts, which can aid in the categorization task. In this work, we propose a novel method for enhancing classi- ﬁcation performance of research papers into ACM computer science categories using knowledge extracted from related Wikipedia articles and Freebase entities. We use state-of-the-art representation learning methods for feature representation of documents, followed by learning to rank method for classiﬁcation. Given the abstracts of research pa- pers from the Citation Network Dataset containing 0.24M papers, our method of using KB, outperforms a baseline method and the state- of-the-art deep learning method in classiﬁcation task by 13.25% and 5.41% respectively, in terms of accuracy. We have also open-sourced the implementation of the project4.
```

### [6] NOREF — [IJCAI2018] MDL for FCA: Is There a Place for Background Knowledge?

- url: https://ceur-ws.org/Vol-2149/paper5.pdf
- ref: none | ratio: -

**extracted**:

```
The Minimal Description Length (MDL) principle is a pow- erful and well founded approach, which has been successfully applied in a wide range of Data Mining tasks. In this paper we address the problem of pattern mining with MDL. We discuss how constraints – background knowledge on interestingness of patterns – can be embedded into MDL and argue the beneﬁts of MDL over a simple selection of patterns based on measures.
```

### [8] NOREF — [VLDB2012] Scuba: Diving into Data at Facebook

- url: http://www.vldb.org/pvldb/vol6/p1057-wiener.pdf
- ref: none | ratio: -

**extracted**:

```
Facebook takes performance monitoring seriously. Performance issues can impact over one billion users so we track thousands of servers, hundreds of PB of daily network trafﬁc, hundreds of daily code changes, and many other metrics. We require latencies of under a minute from events occuring (a client request on a phone, a bug report ﬁled, a code change checked in) to graphs showing those events on developers’ monitors. Scuba is the data management system Facebook uses for most real-time analysis. Scuba is a fast, scalable, distributed, in-memory database built at Facebook. It currently ingests millions of rows (events) per second and expires data at the same rate. Scuba stores data completely in memory on hundreds of servers each with 144 GB RAM. To process each query, Scuba aggregates data from all servers. Scuba processes almost a million queries per day. Scuba is used extensively for interactive, ad hoc, analysis queries that run in under a second over live data. In addition, Scuba is the workhorse behind Facebook’s code regression analysis, bug report monitoring, ads revenue monitoring, and performance debugging.
```

### [10] REVIEW — [VLDB2020] Local Dampening: Differential Privacy for Non-numeric Queries via Local Sensitivity

- url: http://www.vldb.org/pvldb/vol14/p521-farias.pdf
- ref: semanticscholar | ratio: 0.71

**extracted**:

```
Differential privacy is the state-of-the-art formal definition for data release under strong privacy guarantees. A variety of mechanisms have been proposed in the literature for releasing the noisy out- put of numeric queries (e.g., using the Laplace mechanism), based on the notions of global sensitivity and local sensitivity. However, although there has been some work on generic mechanisms for releasing the output of non-numeric queries using global sensitiv- ity (e.g., the Exponential mechanism), the literature lacks generic mechanisms for releasing the output of non-numeric queries using local sensitivity to reduce the noise in the query output. In this work, we remedy this shortcoming and present the local dampening mechanism. We adapt the notion of local sensitivity for the non-numeric setting and leverage it to design a generic non- numeric mechanism. We illustrate the effectiveness of the local dampening mechanism by applying it to two diverse problems: (i) Influential node analysis. Given an influence metric, we release the top-k most influential nodes while preserving the privacy of the relationship between nodes in the network; (ii) Decision tree induction. We provide a private adaptation to the ID3 algorithm to build decision trees from a given tabular dataset. Experimental results show that we could reduce the use of privacy budget by 3 to 4 orders of magnitude for Influential node analysis and increase accuracy up to 12% for Decision tree induction when compared 
```

**reference**:

```
Differential privacy is the state-of-the-art formal definition for data release under strong privacy guarantees. A variety of mechanisms have been proposed in the literature for releasing the output of numeric queries (e.g., the Laplace mechanism and smooth sensitivity mechanism). Those mechanisms guarantee differential privacy by adding noise to the true query’s output. The amount of noise added is calibrated by the notions of global sensitivity and local sensitivity of the query that measure the impact of the addition or removal of an individual on the query’s output. Mechanisms that use local sensitivity add less noise and, consequently, have a more accurate answer. However, although there has been some work on generic mechanisms for releasing the output of non-numeric queries using global sensitivity (e.g., the Exponential mechanism), the literature lacks generic mechanisms for releasing the output of non-numeric queries using local sensitivity to reduce the noise in the query’s output. In this work, we remedy this shortcoming and present the local dampening mechanism . We adapt the notion of local sensitivity for the non-numeric setting and leverage it to design a generic non-numeric mechanism. We provide theoretical comparisons to the exponential mechanism and show under which conditions the local dampening mechanism is more accurate than the exponential mechanism. We illustrate the effectiveness of the local dampening mechanism by applying it to three diverse problems:
```

### [12] FAIL — [VLDB2023] Aleph Filter: To Infinity in Constant Time

- url: https://www.vldb.org/pvldb/vol17/p3644-dayan.pdf
- ref: openalex | ratio: 0.00 | note: download failed: empty_abstract

**extracted**:

```
(empty)
```

**reference**:

```
Filter data structures are widely used in various areas of computer science to answer approximate set-membership queries. In many applications, the data grows dynamically, requiring their filters to expand along with the data. However, existing methods for expanding filters cannot maintain stable performance, memory footprint, and false positive rate (FPR) simultaneously. We address this problem with Aleph Filter, which makes the following contributions. (1) It supports all operations (insertions, queries, deletes, etc.) in constant time, no matter how much the data grows. (2) Given an estimate of how much the data will ultimately grow, Aleph Filter provides a memory vs. FPR trade-offs on par with static filters.
```

### [23] NOREF — [VLDB2016] Strider: An Adaptive, Inference-enabled Distributed RDF Stream Processing Engine

- url: http://www.vldb.org/pvldb/vol10/p1905-ren.pdf
- ref: none | ratio: -

**extracted**:

```
Real-time processing of data streams emanating from sen- sors is becoming a common task in industrial scenarios. An increasing number of processing jobs executed over such platforms are requiring reasoning mechanisms. The key implementation goal is thus to e(cid:30)ciently handle massive incoming data streams and support reasoning, data ana- lytic services. Moreover, in an on-going industrial project on anomaly detection in large potable water networks, we are facing the e(cid:27)ect of dynamically changing data and work characteristics in stream processing. The Strider system addresses these research and implementation challenges by considering scalability, fault-tolerance, high throughput and acceptable latency properties. We will demonstrate the ben- e(cid:28)ts of Strider on an Internet of Things-based real world and industrial setting.
```
