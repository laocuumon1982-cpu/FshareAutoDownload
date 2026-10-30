# Fshare Auto Download

Tool Windows (Tkinter) quét các thư mục Fshare chứa phim hoạt hình cập nhật hằng ngày, mở tập mới trên Firefox để IDM bắt link và tự bấm "Bắt đầu tải". Tải xong thì copy vào thư mục phim và upload lên PikPak (qua rclone). Có giao diện web cho iPhone.

- Chạy bằng Python: `ChayTool.bat` (cài thư viện trước bằng `CaiDat.bat`).
- Bản exe portable: tải `FshareAutoDownload_Portable.zip` ở mục **Releases**.
- Dữ liệu người dùng nằm trong thư mục `Data` (không đưa lên đây).
- Hướng dẫn: `HUONG_DAN.txt`, cập nhật: `HUONG_DAN_CAP_NHAT.txt`, lịch sử phiên bản: `CHANGELOG.txt`.

Tạo Release mới với tag `vX.Y` thì GitHub Actions tự đóng gói exe; nút "⟳ Cập nhật" trong tool sẽ tự thấy bản mới.
