# Retrieval trace — 20 golden questions, after Q1+Q2

Source: retrieval-diag-q2-dualquery.json (ran 2026-08-26T02:05:30).
Columns: T0 (exact curated question) · dense/BM25 top-1 · rerank top-1
with signals · served entry [tier] · expected.

## اینوتکس چیست؟
- normalized: اینوتکس inotex چیست
- coverage-q: اینوتکس چیست
- T0: inotex-overview (1.0)
- dense top-1: inotex-overview (0.4524)
- bm25 top-1: inotex-overview (1.0)
- rerank top-1: inotex-overview final=0.7005 dense=0.4524 bm25=1.0 cov=1.0
- served: inotex-overview [T0] score=1.0 | expected: inotex-overview | OK

## تاریخ برگزاری اینوتکس ۲۰۲۶
- normalized: تاریخ زمان کی چه زمانی موعد برگزاری اینوتکس inotex ۲۰۲۶
- coverage-q: تاریخ برگزاری اینوتکس ۲۰۲۶
- T0: inotex-date (0.9)
- dense top-1: inotex-date (0.8715)
- bm25 top-1: inotex-date (1.0)
- rerank top-1: inotex-date final=0.9228 dense=0.8715 bm25=1.0 cov=0.75
- served: inotex-date [T0] score=0.9 | expected: inotex-date | OK

## اینوتکس کی برگزار می‌شود؟
- normalized: اینوتکس inotex کی برگزار می شود
- coverage-q: اینوتکس کی برگزار می شود
- T0: inotex-date (1.0)
- dense top-1: inotex-organizers (0.6148)
- bm25 top-1: inotex-date (1.0)
- rerank top-1: inotex-date final=0.643 dense=0.4242 bm25=1.0 cov=1.0
- served: inotex-date [T0] score=1.0 | expected: inotex-date | OK

## محل برگزاری اینوتکس کجاست؟
- normalized: محل برگزاری اینوتکس inotex کجاست کجا محل آدرس مکان
- coverage-q: محل برگزاری اینوتکس کجاست
- T0: inotex-venue (0.875)
- dense top-1: inotex-venue (0.4056)
- bm25 top-1: inotex-venue (1.0)
- rerank top-1: inotex-venue final=0.6215 dense=0.4056 bm25=1.0 cov=0.6667
- served: inotex-venue [T1-questions] score=0.9369 | expected: inotex-venue | OK

## چطور غرفه رزرو کنم؟
- normalized: چطور غرفه استند booth فضای نمایشگاهی رزرو کنم
- coverage-q: چطور غرفه رزرو کنم
- T0: inotex-booth (1.0)
- dense top-1: inotex-booth (1.0)
- bm25 top-1: inotex-booth (1.0)
- rerank top-1: inotex-booth final=1.0 dense=1.0 bm25=1.0 cov=1.0
- served: inotex-booth [T0] score=1.0 | expected: inotex-booth | OK

## هزینه غرفه چقدر است؟
- normalized: هزینه قیمت تعرفه مبلغ نرخ غرفه استند booth فضای نمایشگاهی چقدر است
- coverage-q: هزینه غرفه چقدر است
- T0: inotex-booth (0.8571)
- dense top-1: inotex-booth (0.5001)
- bm25 top-1: inotex-booth (1.0)
- rerank top-1: inotex-booth final=0.6551 dense=0.5001 bm25=1.0 cov=0.5
- served: inotex-booth [T1-questions] score=1.0 | expected: inotex-booth | OK

## غرفه مجازی دارید؟
- normalized: غرفه استند booth فضای نمایشگاهی مجازی دارید
- coverage-q: غرفه مجازی دارید
- T0: inotex-booth (0.75)
- dense top-1: inotex-booth (0.9948)
- bm25 top-1: inotex-booth (1.0)
- rerank top-1: inotex-booth final=0.9868 dense=0.9948 bm25=1.0 cov=0.6667
- served: inotex-booth [T1] score=0.9868 | expected: inotex-booth | OK

## چه کسانی می‌توانند غرفه‌دار شوند؟
- normalized: چه کسانی می توانند غرفه دار غرفه دار مشارکت کننده مشارکت کننده نمایشگر شوند
- coverage-q: چه کسانی می توانند غرفه دار شوند
- T0: inotex-exhibitors (1.0)
- dense top-1: inotex-exhibitors (0.0923)
- bm25 top-1: inotex-exhibitors (1.0)
- rerank top-1: inotex-exhibitors final=0.4772 dense=0.0923 bm25=1.0 cov=1.0
- served: inotex-exhibitors [T0] score=1.0 | expected: inotex-exhibitors | OK

## چه برنامه‌هایی در اینوتکس هست؟
- normalized: چه برنامه هایی در اینوتکس inotex هست
- coverage-q: چه برنامه هایی در اینوتکس هست
- T0: no
- dense top-1: inotex-app (0.3647)
- bm25 top-1: inotex-targeted-visit (1.0)
- rerank top-1: inotex-app final=0.5259 dense=0.3647 bm25=0.8688 cov=0.6667
- served: inotex-programs [T1-questions] score=0.9309 | expected: inotex-programs | OK

## اینوتکس پیچ چیست؟
- normalized: پیچ اینوتکس اینوتکس پیچ رقابت استارتاپی بتل چیست
- coverage-q: اینوتکس پیچ چیست
- T0: inotex-pitch (1.0)
- dense top-1: inotex-pitch (1.0)
- bm25 top-1: inotex-pitch (1.0)
- rerank top-1: inotex-pitch final=1.0 dense=1.0 bm25=1.0 cov=1.0
- served: inotex-pitch [T0] score=1.0 | expected: inotex-pitch | OK

## رقابت استارتاپی کی برگزار می‌شود؟
- normalized: رقابت استارتاپی کی برگزار می شود
- coverage-q: رقابت استارتاپی کی برگزار می شود
- T0: inotex-date (0.5)
- dense top-1: inotex-pitch (0.7764)
- bm25 top-1: inotex-pitch (1.0)
- rerank top-1: inotex-pitch final=0.9013 dense=0.7764 bm25=1.0 cov=1.0
- served: inotex-pitch [T1] score=0.9013 | expected: inotex-pitch | OK

## کافه سرمایه چیست؟
- normalized: کافه سرمایه چیست
- coverage-q: کافه سرمایه چیست
- T0: inotex-programs (1.0)
- dense top-1: inotex-programs (0.0)
- bm25 top-1: inotex-programs (1.0)
- rerank top-1: inotex-programs final=0.42 dense=0.0 bm25=1.0 cov=1.0
- served: inotex-programs [T0] score=1.0 | expected: inotex-programs | OK

## کی میتونم بیام نمایشگاه؟
- normalized: کی میتونم بیام نمایشگاه
- coverage-q: کی میتونم بیام نمایشگاه
- T0: no
- dense top-1: inotex-booth (0.0)
- bm25 top-1: inotex-date (1.0)
- rerank top-1: inotex-targeted-visit final=0.221 dense=0.0 bm25=0.7437 cov=0.3333
- served: None [none] score=0.221 | expected: inotex-date | defer

## چجوری واسه غرفه اقدام کنم؟
- normalized: چجوری واسه غرفه استند booth فضای نمایشگاهی اقدام کنم
- coverage-q: چجوری واسه غرفه اقدام کنم
- T0: inotex-booth (0.5455)
- dense top-1: inotex-booth (0.8452)
- bm25 top-1: inotex-booth (1.0)
- rerank top-1: inotex-booth final=0.8315 dense=0.8452 bm25=1.0 cov=0.25
- served: inotex-booth [T1] score=0.8315 | expected: inotex-booth | OK

## مسابقه استارتاپا چیه؟
- normalized: مسابقه استارتاپا چیه
- coverage-q: مسابقه استارتاپا چیه
- T0: no
- dense top-1: inotex-pitch (0.6)
- bm25 top-1: inotex-pitch (1.0)
- rerank top-1: inotex-pitch final=0.717 dense=0.6 bm25=1.0 cov=0.5
- served: inotex-pitch [T1] score=0.717 | expected: inotex-pitch | OK

## نمایشگاه کجا هست دقیقا؟
- normalized: نمایشگاه کجا هست دقیقا
- coverage-q: نمایشگاه کجا هست دقیقا
- T0: no
- dense top-1: inotex-targeted-visit (0.0041)
- bm25 top-1: inotex-venue (1.0)
- rerank top-1: inotex-overview final=0.1729 dense=0.0 bm25=0.4258 cov=0.5
- served: inotex-venue [T1-questions] score=0.7283 | expected: inotex-venue | OK

## What is INOTEX?
- normalized: what is inotex اینوتکس
- coverage-q: what is inotex
- T0: inotex-overview (1.0)
- dense top-1: inotex-overview (0.4766)
- bm25 top-1: inotex-app (1.0)
- rerank top-1: inotex-overview final=0.6744 dense=0.4766 bm25=0.9954 cov=1.0
- served: inotex-overview [T0] score=1.0 | expected: inotex-overview | OK

## When is INOTEX 2026 held?
- normalized: when is inotex اینوتکس 2026 held
- coverage-q: when is inotex 2026 held
- T0: inotex-date (0.6667)
- dense top-1: inotex-date (0.2424)
- bm25 top-1: inotex-app (1.0)
- rerank top-1: inotex-organizers final=0.3723 dense=0.1701 bm25=0.9429 cov=0.3333
- served: inotex-venue [T1-questions] score=0.8145 | expected: inotex-date | WRONG

## Where is the venue?
- normalized: where is the venue
- coverage-q: where is the venue
- T0: no
- dense top-1: inotex-booth (0.0)
- rerank top-1: inotex-booth final=0.0 dense=0.0 bm25=0.0 cov=0.0
- served: inotex-venue [T1.5] score=0.6579 | expected: inotex-venue | OK

## How do I book a booth?
- normalized: how do i book a booth
- coverage-q: how do i book a booth
- T0: no
- dense top-1: inotex-booth (0.1821)
- bm25 top-1: inotex-booth (1.0)
- rerank top-1: inotex-booth final=0.4579 dense=0.1821 bm25=1.0 cov=0.5
- served: inotex-booth [T1-questions] score=0.7732 | expected: inotex-booth | OK
