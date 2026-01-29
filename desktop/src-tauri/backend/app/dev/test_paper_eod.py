from app.jobs.paper_trade_eod import paper_trade_eod_job

def main():
    print("🔔 Running EOD paper trade square-off test")
    paper_trade_eod_job()
    print("✅ EOD paper trade job completed")

if __name__ == "__main__":
    main()
