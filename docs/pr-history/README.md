# Pull Request Work History

Tài liệu lưu lại lịch sử triển khai theo từng PR. Mỗi PR có một file riêng để có thể cập nhật sau mỗi lần làm việc.

## Quy ước

- Mỗi file ghi mục tiêu, thay đổi, nhánh/commit, trạng thái merge, CI/test evidence và giới hạn đã biết.
- Trạng thái và commit SHA lấy từ GitHub API khi tạo bản ghi; trước khi dùng làm release/audit evidence, nên đối chiếu lại PR và workflow.
- Không tự suy diễn CI/commit khi không có bằng chứng.
- **PR #1:** GitHub API trả về 404 khi truy vấn Pull Request #1. Repository có roadmap Issue #1, nên không ghi bịa nội dung PR #1; xem [PR-01.md](PR-01.md).
- **PR #13:** trạng thái hiện tại lúc ghi nhận là Draft/Open, chưa merge.

## Danh sách

| File | PR | Chủ đề | Trạng thái tại thời điểm ghi |
|---|---:|---|---|
| [PR-01.md](PR-01.md) | #1 | Không có PR #1 truy xuất được; ghi chú minh bạch | Không áp dụng |
| [PR-02.md](PR-02.md) | #2 | Python project foundation | Merged |
| [PR-03.md](PR-03.md) | #3 | Core domain/configuration | Merged |
| [PR-04.md](PR-04.md) | #4 | Public market-data adapters/health checks | Merged |
| [PR-05.md](PR-05.md) | #5 | Depth-aware spot arbitrage scanner | Merged |
| [PR-06.md](PR-06.md) | #6 | Market constraints/paper fills | Merged |
| [PR-07.md](PR-07.md) | #7 | Pre-trade gates/SQLite audit | Merged |
| [PR-08.md](PR-08.md) | #8 | Paper portfolio/risk controls | Merged |
| [PR-09.md](PR-09.md) | #9 | Portfolio persistence/recovery | Merged |
| [PR-10.md](PR-10.md) | #10 | Market snapshot/replay | Merged |
| [PR-11.md](PR-11.md) | #11 | Reporting/evaluation | Merged |
| [PR-12.md](PR-12.md) | #12 | Persistent paper kill switch | Merged |
| [PR-13.md](PR-13.md) | #13 | Latency/fill-probability/legging risk | Draft/Open — not merged |

## Nguồn liên quan

- [Roadmap Issue #1](https://github.com/nguyenngocbinh/arb-foundry/issues/1)
- [Repository](https://github.com/nguyenngocbinh/arb-foundry)
- [PR #13](https://github.com/nguyenngocbinh/arb-foundry/pull/13)

## Quy trình cập nhật mỗi lần làm PR

Sau khi làm việc trên PR:
1. Cập nhật file PR tương ứng với mục tiêu và diff thực tế.
2. Ghi final head SHA, base SHA và merge SHA (nếu đã merge).
3. Ghi CI run URL, kết quả test theo từng Python version, coverage và lint.
4. Ghi lỗi ban đầu, cách sửa, giới hạn/rủi ro còn lại.
5. Cập nhật trạng thái trong bảng này sau khi xác minh GitHub; không đánh dấu merged trước khi GitHub xác nhận.
