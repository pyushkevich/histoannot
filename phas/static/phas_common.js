  // This function reformats the header json into something I can display in a table
  function parseJSONDictionary(jsonDict) {
    const jsonArray = [];
    // Iterate over the keys of the JSON object
    for (const key in jsonDict) {
        if (jsonDict.hasOwnProperty(key)) {
            // Create a dictionary with 'key' and 'value' entries
            const dictEntry = {
                "key": key,
                "value": jsonDict[key]
            };
            // Push the dictionary to the array
            jsonArray.push(dictEntry);
        }
    }
    return jsonArray;
}

// This splits a key into a group part and a display part
function split_propkey_group_subkey(key) {
    // If starts with tiff.ImageDescription, group as 'ImageDescription'
    if (key.startsWith('tiff.ImageDescription.')) {
        return { group: 'TIFF Image Description', key: key.replace('tiff.ImageDescription.', '') };
    }
    else if (key.startsWith('tiff.')) {
        return { group: 'TIFF Tags', key: key.replace('tiff.', '') };
    }
    else if (key.startsWith('mirax.GENERAL')) {
        return { group: 'MIRAX General Tags', key: key.replace('mirax.GENERAL.', '') };
    }
    else if (key.startsWith('openslide.')) {
        return { group: 'OpenSlide Properties', key: key.replace('openslide.', '') };
    }
    else {
        return { group: 'Other Properties', key: key };
    }
}

// This function generates a properties data table
function make_slide_properties_table(table_html) {
    let table = table_html.DataTable( {
        data: [],
        ajax: {
            url: "",
            dataSrc: function(resp_text) {
              console.log('Response text:', resp_text);
              if(!jQuery.isEmptyObject(resp_text))
                return parseJSONDictionary(resp_text['properties'])
              else
                return [];
            },
        },
        columns: [
            { 
                data: "key",
                render: function (data, type, row, meta) {
                    if(type === 'display') {
                      const { group, key } = split_propkey_group_subkey(data);
                      return key;
                    }
                    else if(type === 'sort') {
                      const { group, key } = split_propkey_group_subkey(data);
                      return group + '.' + key;
                    }
                    else {
                      return data;
                    }
                }
            },
            { 
                data: "value", 
                render: function (data, type, row, meta) {
                    return typeof(data) == 'string' ? data.replaceAll('\n','<br>') : data;
                }
            }
        ],
        info: false,
        paging: false,
        select: 'none',
        scrollY: '400px',
        rowGroup: {
            dataSrc: function(row) {
              // If starts with tiff.ImageDescription, group as 'ImageDescription'
              const { group, key } = split_propkey_group_subkey(row.key);
              return group;
            }
        }
      } );
    return table;
}