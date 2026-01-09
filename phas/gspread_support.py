import gspread
import pandas as pd
import os

class GoogleSheetManifest:
    
    def __init__(self, sheet_name, credentials_json = None):
        
        # If no credentials file is provided, read from environment variable
        if credentials_json is None:
            credentials_json = os.environ.get('PHAS_GDRIVE_APPLICATION_CREDENTIALS', None)
            if credentials_json is None:
                raise ValueError("Google Sheets credentials JSON file must be provided either as an argument or via the GOOGLE_APPLICATION_CREDENTIALS environment variable.")
        
        # Create a client to interact with the Google Drive API
        gc = gspread.service_account(filename=credentials_json)
        
        # Load the spreadsheet
        self.sh = gc.open(sheet_name)
        
        # Read all the worksheets
        self.worksheets_ = [ x.title for x in self.sh.worksheets() ]
        
    def worksheets(self):
        return self.worksheets_
    
    def get_worksheet(self, title):
        match = self.sh.worksheet(title)
        recs = match.get_all_records()
        if len(recs) == 0:
            return None
        
        # Create a new frame with the required fields converted to numeric format
        df = pd.DataFrame(recs)
        df_num = pd.DataFrame({
            'Slide': df.Slide.astype('str'),
            'Stain': df.Stain,
            'Block': df.Block,
            'Section': pd.to_numeric(df.Section, 'coerce', downcast='signed').astype('Int64'),
            'Slice': pd.to_numeric(df.Slice, 'coerce', downcast='signed').astype('Int64'),
            'Certainty': df.Certainty,
            'Tags': df.Tags
        })
            
        return df_num
