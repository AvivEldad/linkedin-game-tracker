import pandas as pd
import sys

def validate_data():
    csv_file = "games_log.csv"
  
    try:
        # 1. Try reading the file
        df = pd.read_csv(csv_file)
        
        # 2. Check structure (Exactly 8 columns)
        if len(df.columns) != 8:
            print(f"❌ Error: Expected 8 columns, found {len(df.columns)}: {list(df.columns)}")
            sys.exit(1)
            
        # 3. Check for completely empty/null rows
        if df.isnull().all(axis=1).any():
            print("❌ Error: Found completely empty rows in the CSV.")
            sys.exit(1)
            
        # 4. Add your own custom business rules here. Examples:
        # if df['id'].duplicated().any():
        #     print("❌ Error: Duplicate IDs found.")
        #     sys.exit(1)
            
        print("✅ Success: CSV passed all data tests!")
        sys.exit(0)
        
    except Exception as e:
        print(f"❌ Critical Error reading CSV: {e}")
        sys.exit(1)

if __name__ == "__main__":
    validate_data()
